def calculate_march_variance(*, actual: int, planned: int) -> str:
    variance = actual - planned
    percentage = variance / planned * 100
    return f"actual={actual}, planned={planned}, variance={variance} ({percentage:.1f}%)"
