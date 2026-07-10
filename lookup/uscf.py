"""
USCF (US Chess Federation) public member lookup — used to cross-check a
tournament entrant's actual FIDE nationality against an online profile's
listed country. A much more reliable confidence signal than assuming every
entrant is US-based just because the tournament is US-run: plenty of
entrants in US-run events are international players or residents.

Usage:
  python -m lookup.uscf 12345678
"""

import re
import sys
import json
import argparse

import requests

_BASE = "https://www.uschess.org/msa/MbrDtlMain.php"
_HEADERS = {"User-Agent": "chess-dossier-builder/1.0"}

_FIDE_COUNTRY_RE = re.compile(r"FIDE Country</td>\s*<td>\s*<b>\s*([A-Za-z]{3})\s*</b>", re.IGNORECASE)

# FIDE federation codes are IOC-style 3-letter codes, not strict ISO 3166-1
# alpha-3 — they mostly agree (e.g. "PAR" Paraguay, "TUR" Turkey) but not
# always (e.g. FIDE/IOC "GER"/"NED" vs ISO "DEU"/"NLD"), and FIDE has a few
# federations with no ISO equivalent at all (the UK's home nations compete
# separately: ENG/SCO/WLS/IOM). Values are ISO 3166-1 alpha-2, matching what
# Lichess (`profile.flag`) and chess.com (`country`) actually expose.
_FIDE_TO_ISO2 = {
    "AFG": "AF", "ALB": "AL", "ALG": "DZ", "AND": "AD", "ANG": "AO", "ARG": "AR",
    "ARM": "AM", "ARU": "AW", "AUS": "AU", "AUT": "AT", "AZE": "AZ", "BAN": "BD",
    "BAR": "BB", "BEL": "BE", "BER": "BM", "BIH": "BA", "BOL": "BO", "BOT": "BW",
    "BRA": "BR", "BRN": "BH", "BRU": "BN", "BUL": "BG", "BUR": "BF", "CAF": "CF",
    "CAM": "KH", "CAN": "CA", "CHI": "CL", "CHN": "CN", "CIV": "CI", "CMR": "CM",
    "COD": "CD", "COL": "CO", "CRC": "CR", "CRO": "HR", "CUB": "CU", "CYP": "CY",
    "CZE": "CZ", "DEN": "DK", "DOM": "DO", "ECU": "EC", "EGY": "EG", "ENG": "GB",
    "ESA": "SV", "ESP": "ES", "EST": "EE", "ETH": "ET", "FID": None, "FIJ": "FJ",
    "FIN": "FI", "FRA": "FR", "GAB": "GA", "GAM": "GM", "GEO": "GE", "GER": "DE",
    "GHA": "GH", "GRE": "GR", "GRN": "GD", "GUA": "GT", "GUM": "GU", "GUY": "GY",
    "HAI": "HT", "HKG": "HK", "HON": "HN", "HUN": "HU", "INA": "ID", "IND": "IN",
    "IOM": "IM", "IRI": "IR", "IRL": "IE", "IRQ": "IQ", "ISL": "IS", "ISR": "IL",
    "ISV": "VI", "ITA": "IT", "IVB": "VG", "JAM": "JM", "JOR": "JO", "JPN": "JP",
    "KAZ": "KZ", "KEN": "KE", "KGZ": "KG", "KOR": "KR", "KOS": "XK", "KSA": "SA",
    "KUW": "KW", "LAO": "LA", "LAT": "LV", "LBA": "LY", "LBR": "LR", "LCA": "LC",
    "LES": "LS", "LIB": "LB", "LIE": "LI", "LTU": "LT", "LUX": "LU", "MAC": "MO",
    "MAD": "MG", "MAR": "MA", "MAS": "MY", "MAW": "MW", "MDA": "MD", "MEX": "MX",
    "MGL": "MN", "MKD": "MK", "MLI": "ML", "MLT": "MT", "MNE": "ME", "MOZ": "MZ",
    "MRI": "MU", "MTN": "MR", "MYA": "MM", "NAM": "NA", "NCA": "NI", "NED": "NL",
    "NEP": "NP", "NGR": "NG", "NOR": "NO", "NZL": "NZ", "OMA": "OM", "PAK": "PK",
    "PAN": "PA", "PAR": "PY", "PER": "PE", "PHI": "PH", "PLE": "PS", "PNG": "PG",
    "POL": "PL", "POR": "PT", "PUR": "PR", "QAT": "QA", "ROU": "RO", "RSA": "ZA",
    "RUS": "RU", "RWA": "RW", "SCO": "GB", "SEN": "SN", "SEY": "SC", "SIN": "SG",
    "SLE": "SL", "SLO": "SI", "SOL": "SB", "SOM": "SO", "SRB": "RS", "SRI": "LK",
    "SSD": "SS", "STP": "ST", "SUD": "SD", "SUI": "CH", "SUR": "SR", "SVK": "SK",
    "SWE": "SE", "SWZ": "SZ", "SYR": "SY", "TAN": "TZ", "TGA": "TO", "THA": "TH",
    "TJK": "TJ", "TKM": "TM", "TLS": "TL", "TOG": "TG", "TPE": "TW", "TTO": "TT",
    "TUN": "TN", "TUR": "TR", "UAE": "AE", "UGA": "UG", "UKR": "UA", "URU": "UY",
    "USA": "US", "UZB": "UZ", "VAN": "VU", "VEN": "VE", "VIE": "VN", "VIN": "VC",
    "WLS": "GB", "YEM": "YE", "ZAM": "ZM", "ZIM": "ZW",
}


def get_fide_country(uscf_id: str) -> str | None:
    """
    Fetch a USCF member's FIDE federation and convert it to an ISO alpha-2
    country code (e.g. "TR" for Turkey), for comparing against a Lichess/
    chess.com profile's country. Returns None if the member has no FIDE
    Country on file (true for most club-level players — not an error) or
    the federation code isn't in the lookup table.
    """
    resp = requests.get(_BASE, params={uscf_id: ""}, headers=_HEADERS, timeout=15)
    resp.raise_for_status()
    m = _FIDE_COUNTRY_RE.search(resp.text)
    if not m:
        return None
    return _FIDE_TO_ISO2.get(m.group(1).upper())


def main() -> None:
    parser = argparse.ArgumentParser(description="Look up a USCF member's FIDE nationality.")
    parser.add_argument("uscf_id")
    args = parser.parse_args()

    country = get_fide_country(args.uscf_id)
    print(json.dumps({"uscf_id": args.uscf_id, "fide_country": country}, indent=2))


if __name__ == "__main__":
    main()
