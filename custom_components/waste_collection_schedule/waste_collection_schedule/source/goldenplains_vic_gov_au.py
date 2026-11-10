import re
from datetime import datetime

from bs4 import BeautifulSoup
from curl_cffi import requests
from waste_collection_schedule import Collection  # type: ignore[attr-defined]
from waste_collection_schedule.exceptions import (
    SourceArgumentNotFoundWithSuggestions,
)

TITLE = "Golden Plains Shire Council"
URL = "https://www.goldenplains.vic.gov.au"
DESCRIPTION = "Source for Golden Plains Shire Council, VIC, AU"
COUNTRY = "au"

TEST_CASES = {
    "2 Pope Street Bannockburn": {"address": "2 POPE STREET BANNOCKBURN 3331"},
}

AUTOCOMPLETE_URL = "https://www.goldenplains.vic.gov.au/views-autocomplete-filters/addresses/page_3/ezi_address/0"
SCHEDULE_URL = "https://www.goldenplains.vic.gov.au/address-search"

# Maps the CSS class on each card div to a (waste type label, MDI icon) tuple
CARD_TYPES = {
    "recycling": ("Recycling", "mdi:recycle"),
    "rubbish": ("Garbage", "mdi:trash-can"),
    "fogo": ("Glass", "mdi:bottle-wine"),
}


def _address_key(address: str) -> str:
    """Reduce an address to a form that does not depend on how the council writes it.

    The council's address search is a case-insensitive "contains" match and returns
    addresses such as "48 Giles Drive Bannockburn Vic 3331" (the state is written
    "Vic" or "VIC" and has changed before), so a user-entered
    "48 GILES DRIVE BANNOCKBURN 3331" finds nothing. Case, commas, the state and the
    postcode are therefore ignored when searching and comparing.
    """
    key = re.sub(r"[,\s]+", " ", address).strip().upper()
    # only a trailing state and/or postcode: "VICTORIA STREET" must be kept
    key = re.sub(r"(\s+(VIC|VICTORIA|\d{4}))+$", "", key)
    return re.sub(r"\s+", " ", key).strip()


class Source:
    def __init__(self, address: str):
        self._address = re.sub(r"\s+", " ", address).strip()

    def _suggestions(self, session, query: str) -> list[dict]:
        r = session.get(AUTOCOMPLETE_URL, params={"q": query}, timeout=30)
        r.raise_for_status()
        return r.json() or []

    def fetch(self) -> list[Collection]:
        # Step 1: find the address as the council writes it
        session = requests.Session(impersonate="chrome")
        wanted = _address_key(self._address)

        # Search for the address without state/postcode first (that is always a
        # substring of the council's form); fall back to exactly what was entered.
        suggestions: list[dict] = []
        canonical = None
        for query in dict.fromkeys([wanted, self._address]):
            if not query:
                continue
            found = self._suggestions(session, query)
            suggestions = suggestions or found
            canonical = next(
                (i["value"] for i in found if _address_key(i["value"]) == wanted),
                None,
            )
            if canonical is not None:
                break

        if canonical is None:
            raise SourceArgumentNotFoundWithSuggestions(
                "address",
                self._address,
                [item["value"] for item in suggestions],
            )

        # Step 2: fetch the schedule page using the canonical address
        r = session.get(
            SCHEDULE_URL,
            params={"address": canonical},
            timeout=30,
        )
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")

        entries: list[Collection] = []

        for css_class, (waste_type, icon) in CARD_TYPES.items():
            card = soup.select_one(f"div.card.{css_class}")
            if not card:
                continue

            p = card.find("p")
            if not p:
                continue

            # Dates are separated by <br> tags, e.g. "27th April 2026"
            for date_str in p.stripped_strings:
                date_str = date_str.strip()
                if not date_str:
                    continue
                # Strip ordinal suffixes: 1st 2nd 3rd 4th etc.
                date_clean = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", date_str)
                try:
                    date = datetime.strptime(date_clean, "%d %B %Y").date()
                except ValueError:
                    continue
                entries.append(Collection(date=date, t=waste_type, icon=icon))

        return entries
