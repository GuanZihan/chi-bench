apptainer run \
  --contain \
  --cleanenv \
  --no-eval \
  --bind "$PWD:/workspace" \
  --pwd /workspace \
  --env INJECT_NETWORK_ENABLED=1 \
  --env NETWORK_FAILURE_RATE=0.01 \
  --env NOISY_ERROR_MODE=5xx \
  --env BLOCKED_DOMAINS=example.com \
  containers/noisy-container_latest.sif \
  bash