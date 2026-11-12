"""Shared form data for the settings page tests."""


def valid_form(**overrides: str) -> dict[str, str]:
    form = {
        "electricity_price": "0,30",
        "labour_rate": "15,00",
        "scrap_rate": "5",
        "markup": "30",
        "rounding": "ends_90",
        "spool_category": "Geschäftlich",
        "deviation_threshold": "15",
    }
    form.update(overrides)
    return form
