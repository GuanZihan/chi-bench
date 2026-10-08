# chi-bench on Rivanna

这条路径使用 Apptainer，不需要 Docker daemon，也不用 sudo。先跑通原始任务和原始 evaluation；这里没有新增 fault injection。

## 1. 准备数据和共享 SIF

先申请一个 CPU compute node；调用远程模型时不需要 GPU。建议至少 4 CPU、16 GB 内存，并预留镜像构建时间。下面的命令从 `chi-bench/` 运行：

```bash
uv sync --extra dev
uv run cb data verify
bash containers/build_sif.sh
```

数据下载沿用主 README 的 **Download / Setup** 步骤：`data/` 中需要三个单域任务家族，以及单独申请访问权限后下载的 `data/skills/managed-care-operations-handbook/SKILL.md`。E2E 和 marathon 也从同一个 fixtures dataset 下载。

构建产物是 `containers/chi-bench.sif` 和 SHA256 文件。三个单域家族、E2E、marathon 使用同一个镜像，任务数据在运行时挂载；镜像不含 handbook、任务答案或 API key。镜像预装论文配置使用的 Claude Code `2.1.216` 和 Codex CLI `0.145.0`；指定其他 agent version 时 Harbor 仍可能按原逻辑下载对应版本。`Bootstrap: docker` 只表示从 OCI registry 拉取基础镜像，不会启动 Docker。

构建时默认用 2 个 squashfs worker、512 MB squashfs 缓存，避免按整台节点内存分配。可用 `BUILD_CPUS=4 BUILD_MEMORY=1G` 调整。构建临时目录默认放在项目的 `.cache/apptainer-build/`，不用可能占用 Slurm 内存配额的 `/tmp`；可用 `BUILD_TMPDIR` 指到磁盘 scratch。`APPTAINER_IGNORE_PROOT=1` 避开 Rivanna 的 proot/ptrace 限制，临时目录在退出后清理。已有 SIF 默认复用；代码或依赖变化后运行 `bash containers/build_sif.sh --force`，旧镜像会改名保留。

### 数据修正与 Playwright

主 README 要求的 `pa_t035` 修正和 payer request PDF 重生成，在这里可以放到 SIF 内执行，使用镜像安装好的 Chromium 和系统库：

```bash
bash containers/prepare_data.sh
```

这一步会更新本地 `data/` 中相应的 PDF、world/expectations 等文件，作用与原来的两个 repair scripts 一样；不是环境错误注入。若要保留下载时的原版，先备份数据。不要在任务运行期间执行修正。运行时会记录所用输入的 hash，后续比较实验须使用相同的数据版本。

`CHI_BENCH_SIF=/absolute/path/runtime.sif` 可覆盖构建、数据修正和 smoke 脚本使用的镜像；正式实验的自定义镜像路径在 YAML 的 `apptainer.image` 设置。数据修正的自定义数据根目录用 `CHI_BENCH_DATA_DIR`，正式实验则用 `apptainer.data_dir`。

## 2. 不花模型费用的环境检查

```bash
bash containers/smoke_test.sh
```

它启动一个真实 UM task，检查 `/workspace`、PDF 渲染、三个 MCP 的 initialize/list_tools、verifier 文件上传和 state export，然后关闭该 trial 并检查端口释放。不调用 agent LLM 或 judge，因此通过不等于任务已完成或评分已通过。

输出在 `logs/smoke/apptainer-*/`：

- `smoke.json`：检查结果。
- `artifacts/apptainer.log`：容器/bootstrap/命令服务日志。
- `artifacts/chi-bench-server.log`：模拟器和 MCP 日志。
- `apptainer-runtime.json`：镜像/输入 hash、节点、Slurm job ID、临时目录。

检查其他任务时，追加 `--task data/<family>/tasks/<task-id>`；marathon 使用 `--task data/marathon/<family>`。`--keep-runtime` 仅供排查：进程照常停止，但临时数据保留，路径在 manifest 中。

## 3. 跑一个真正的 agent trial

在 `.env` 配置你要用的模型的 key。**即使用 GPT 做 agent，原始 workspace judge 仍默认使用 `claude-opus-4-7`，需要 `ANTHROPIC_API_KEY`。** 请不要提交 `.env`，也不用 `source .env`；runner 会读取 dotenv。

```bash
bash containers/run_task.sh \
  --dataset data/prior_auth_um/tasks/pa_t008_t008_o002_p01_mdreview_payer \
  --agent codex --model openai/gpt-5.5 \
  --trials-dir logs/experiments/apptainer-smoke
```

模型只是示例，改为你账户实际可用的 ID。也可以编辑 `configs/experiments/apptainer_example.yaml` 再运行：

```bash
bash containers/run_task.sh -f configs/experiments/apptainer_example.yaml
```

使用 YAML 时，请在 YAML 内改 `model`、`dataset`、`trials_dir` 等字段，不要假设同名 CLI 参数会覆盖它们。`run_task.sh` 强制选择 Apptainer，其余沿用现有 `cb experiment run`。

## 4. 跑三个任务家族

把单任务路径换为 `tasks/` 目录即可。每个目录的任务会串行执行：

```bash
for family in prior_auth_provider prior_auth_um care_management; do
  bash containers/run_task.sh \
    --dataset "data/$family/tasks" \
    --agent codex --model openai/gpt-5.5 \
    --trials-dir "logs/experiments/apptainer-control/$family"
done
```

多次独立重复实验在 YAML 设置 `n_attempts`，适用于任务目录运行。E2E 需要原有 `dual-pa-e2e` harness 及 provider/payer 模型配置，不能只把普通单 agent 指向 E2E 任务；参考原来的 E2E config，将展开后的单个 `ExperimentConfig` 的 `environment` 设为 `apptainer`、`concurrency` 设为 `1`。marathon 的单任务路径是 `data/marathon/<family>`。

这一版接通的是 `cb experiment run`，不扩展 `cb submission` 或 `scripts/run_table.sh` 的专用参数。不要直接把原来的 paper-table matrix YAML 当作单个 ExperimentConfig 传入。

## 5. 通过 Slurm 跑

从 `chi-bench/` 提交：

```bash
sbatch containers/run_trial.slurm -f configs/experiments/apptainer_example.yaml
```

默认 `bii`、4 CPU、16 GB、72 小时，无 GPU，并申请独占节点。独占节点是为了保证固定的 MCP 端口不会与同节点的 HealthAgentBench/其他 chi-bench 进程冲突，可能按整个节点计费。单个家族的 25 个任务在该节点串行执行；也可在提交时覆盖资源：

```bash
sbatch --time=72:00:00 --mem=32G containers/run_trial.slurm \
  --dataset data/prior_auth_um/tasks \
  --agent codex --model openai/gpt-5.5 \
  --trials-dir logs/experiments/apptainer-um
```

若从别处提交，先 export `CHI_BENCH_ROOT` 为 chi-bench checkout 的绝对路径。日志 `chi-bench-<job-id>.out` 在提交目录，不依赖 Slurm spool 中脚本的位置。

## 隔离和清理的边界

- 每个 trial 获得独立的 workspace、SQLite/state、raw artifacts 和 handbook 副本；当前 task fixtures 和 worlds 只读挂载。下载数据不会被 agent 写回。
- 不把宿主机 home、整个 checkout 或其他任务 fixtures 自动挂进去；`tests/` 在 verifier 阶段上传。这个容器不是防恶意 agent 的安全沙箱：同一容器内的任务 fixtures 仍可能被主动查找，和原始 runtime 一样，不能声称 ground truth 完全不可见。
- 服务只监听 localhost。Apptainer 共享宿主机网络，固定使用 `8020/8023/8100/8200`，并用 `18023` 作为本节点 trial 锁。**每节点一个 trial，`concurrency: 1`。** 发现已占用端口会报错，不会 kill 其他进程。多节点可以并行；不要用同节点多份 sbatch 绕过这一限制。
- 正常结束、启动失败、Python cancellation 都清理本 trial 的进程组和临时目录；SIF 和结果日志保留。硬杀整个 launcher（例如 `kill -9`）不能保证 Python cleanup 执行，Slurm allocation 结束仍依靠 Slurm 回收其进程；不要广泛 `pkill apptainer`。
- 资源限制由 Slurm allocation 管理，不是逐条强制执行 `task.toml` 中的容器内存/CPU 参数。`--writable-tmpfs` 用于系统写层；workspace、容器 home/tmp 和主要运行数据使用磁盘目录，默认在数据根目录旁的 `.cache/apptainer-runtime/`，可用 YAML `apptainer.runtime_root` 指到有足够空间的 scratch。这样 agent 安装依赖和写临时 PDF 时不会挤占 Apptainer 默认的小型系统写层。

所有任务仍使用原来的 instruction、verifier、judge 配置。后续 fault injection 应作为单独的改动加入，先用这些 control runs 验证环境。
