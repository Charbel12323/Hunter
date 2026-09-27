"""Free-text location -> coarse region ('canada', 'us' or 'other').

Shared by two stages that must agree: the ``regions`` filter decides which
jobs are kept, and notify groups the survivors under flagged subheaders.
Keeping one classifier means a job can never pass the filter as "us" and
then be listed under "Other".
"""

import re

# Full country names are matched case-insensitively. The bare "US"/"U.S."
# abbreviation and the two-letter province/state codes are matched
# case-sensitively (uppercase) so English words like "us", "or", "in", "me"
# or "hi" inside a location string can't be mistaken for a country or state.
_CANADA_KW = re.compile(r"\bcanada\b", re.IGNORECASE)
_US_KW = re.compile(r"\bunited states\b|\bu\.?s\.?a\.?\b", re.IGNORECASE)
_US_ABBR = re.compile(r"\bU\.?S\.?\b")
_CA_CODE = re.compile(r"\b(?:AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT)\b")
_US_CODE = re.compile(
    r"\b(?:AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA"
    r"|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT"
    r"|VT|VA|WA|WV|WI|WY)\b"
)
_CA_CITY = re.compile(
    r"\b(?:Toronto|Vancouver|Montr[eé]al|Ottawa|Calgary|Edmonton|Winnipeg"
    r"|Halifax|Mississauga|Kitchener|Waterloo|Qu[eé]bec)\b",
    re.IGNORECASE,
)
# US counterpart for feeds that give a bare hub city ("San Francisco", "SF").
# Without it the regions filter would drop them as "other".
_US_CITY = re.compile(
    r"\b(?:San Francisco|Bay Area|Silicon Valley|New York|Seattle|Austin|Boston"
    r"|Chicago|Los Angeles|San Jose|Santa Clara|Sunnyvale|Mountain View|Palo Alto"
    r"|Menlo Park|Cupertino|Redmond|Bellevue|Denver|Boulder|Atlanta|Pittsburgh"
    r"|Miami|Dallas|Houston|San Diego|Irvine|Philadelphia|Salt Lake City|Raleigh"
    r"|Phoenix|Minneapolis|Detroit|Nashville|Brooklyn|Jersey City|Hoboken)\b",
    re.IGNORECASE,
)
_US_CITY_ABBR = re.compile(r"\b(?:SF|NYC)\b")


def region(location: str) -> str:
    """Bucket a free-text location into 'canada', 'us' or 'other'.

    Explicit country names win first, then province/state codes, then city
    fallbacks for strings that name a city but no country/code.
    """
    if not location:
        return "other"
    if _CANADA_KW.search(location):
        return "canada"
    if _US_KW.search(location) or _US_ABBR.search(location):
        return "us"
    if _CA_CODE.search(location):
        return "canada"
    if _US_CODE.search(location):
        return "us"
    if _CA_CITY.search(location):
        return "canada"
    if _US_CITY.search(location) or _US_CITY_ABBR.search(location):
        return "us"
    return "other"
