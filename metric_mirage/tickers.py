"""Format Hong Kong stock codes without checking listing status."""

import re


def normalize_hk_ticker(ticker: str | int) -> str:
    """Return five ASCII digits for a nonzero code with an optional .HK suffix.

    Surrounding whitespace and the suffix's letter case are ignored. This
    validates the input format only; it does not establish a real listing.
    """
    if isinstance(ticker, bool) or not isinstance(ticker, (str, int)):
        raise TypeError("Hong Kong ticker must be a string or integer, not a boolean or scalar.")
    if isinstance(ticker, int):
        number = ticker
    else:
        match = re.fullmatch(r"([0-9]{1,5})(?:\.HK)?", ticker.strip(), re.IGNORECASE | re.ASCII)
        if match is None:
            raise ValueError(
                f"Invalid Hong Kong ticker {ticker!r}: expected 1-5 ASCII digits "
                "with an optional .HK suffix."
            )
        number = int(match.group(1))
    if not 1 <= number <= 99999:
        raise ValueError("Invalid Hong Kong ticker: code must be between 1 and 99999.")
    return f"{number:05d}"
