from internal_variance_tools import calculate_march_variance


def main() -> None:
    result = calculate_march_variance(actual=102_800, planned=100_000)
    print(f"MARCH_VARIANCE_OK: {result}")


if __name__ == "__main__":
    main()
