#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
World regions for the Authors page's region filter.

The taxonomy is the United Nations geoscheme (M49) at its continental level,
with the Americas split the way the UN splits them. It is published, widely
used, and assigns every country to exactly one region, which keeps the filter
easy to explain and to check:

  Northern America           United States, Canada (and Bermuda, Greenland)
  Latin America & Caribbean  Mexico, Central and South America, the Caribbean
                             (Puerto Rico and the Cayman Islands included)
  Europe                     including Russia, Ukraine and the UK
  Asia                       East, South, South-East, Central and Western Asia,
                             so Türkiye, Israel, the Gulf states and Cyprus are
                             here, as in the UN scheme
  Africa
  Oceania                    Australia, New Zealand and the Pacific islands

Edge cases follow the UN scheme rather than a judgement call of ours:
Mexico is Latin America, Russia is Europe, Cyprus and Türkiye are Asia,
Taiwan is Asia.

An author gets the region of the country of their most recent affiliation
(see author_region); an author with no usable affiliation has no region and
is left out when a region is chosen.
"""

REGIONS = [
    "Northern America",
    "Latin America & Caribbean",
    "Europe",
    "Asia",
    "Africa",
    "Oceania",
]

_BY_REGION = {
    "Northern America": [
        "United States", "Canada", "Bermuda", "Greenland",
    ],
    "Latin America & Caribbean": [
        "Mexico", "Guatemala", "Belize", "Honduras", "El Salvador", "Nicaragua", "Costa Rica", "Panama",
        "Colombia", "Venezuela", "Ecuador", "Peru", "Bolivia", "Brazil", "Paraguay", "Uruguay", "Argentina",
        "Chile", "Guyana", "Suriname", "Cuba", "Dominican Republic", "Haiti", "Jamaica",
        "Trinidad and Tobago", "Bahamas", "Barbados", "Puerto Rico", "Cayman Islands", "KY",
    ],
    "Europe": [
        "United Kingdom", "Ireland", "France", "Germany", "Netherlands", "Belgium", "Luxembourg",
        "Switzerland", "Austria", "Liechtenstein", "Monaco", "Italy", "Spain", "Portugal", "Andorra",
        "Malta", "San Marino", "Greece", "Albania", "North Macedonia", "Serbia", "Montenegro",
        "Bosnia and Herzegovina", "Croatia", "Slovenia", "Hungary", "Slovakia", "Czechia", "Poland",
        "Romania", "Bulgaria", "Moldova", "Ukraine", "Belarus", "Russia", "Lithuania", "Latvia",
        "Estonia", "Finland", "Sweden", "Norway", "Denmark", "Iceland", "Kosovo",
    ],
    "Asia": [
        "China", "Hong Kong", "Macao", "Taiwan", "Japan", "South Korea", "North Korea", "Mongolia",
        "India", "Pakistan", "Bangladesh", "Sri Lanka", "Nepal", "Bhutan", "Maldives", "Afghanistan",
        "Iran", "Singapore", "Malaysia", "Indonesia", "Thailand", "Vietnam", "Philippines", "Cambodia",
        "Laos", "Myanmar", "Brunei", "Kazakhstan", "Uzbekistan", "Kyrgyzstan", "Tajikistan",
        "Turkmenistan", "Turkey", "Türkiye", "Israel", "Palestine", "Lebanon", "Jordan", "Syria", "Iraq",
        "Saudi Arabia", "United Arab Emirates", "Qatar", "Kuwait", "Bahrain", "Oman", "Yemen",
        "Cyprus", "Armenia", "Azerbaijan", "Georgia",
    ],
    "Africa": [
        "Egypt", "Libya", "Tunisia", "Algeria", "Morocco", "Sudan", "South Sudan", "Ethiopia", "Eritrea",
        "Djibouti", "Somalia", "Kenya", "Uganda", "Tanzania", "Rwanda", "Burundi", "Nigeria", "Ghana",
        "Senegal", "Ivory Coast", "Cote d'Ivoire", "Cameroon", "Mali", "Niger", "Burkina Faso", "Benin",
        "Togo", "Sierra Leone", "Liberia", "Guinea", "Gambia", "Mauritania", "Cape Verde", "Chad",
        "Gabon", "Congo", "DR Congo", "Angola", "Zambia", "Zimbabwe", "Malawi", "Mozambique",
        "Madagascar", "Mauritius", "Namibia", "Botswana", "South Africa", "Lesotho", "Eswatini",
    ],
    "Oceania": [
        "Australia", "New Zealand", "Fiji", "Papua New Guinea", "Samoa", "Tonga", "Vanuatu",
        "Solomon Islands",
    ],
}

COUNTRY_REGION = {country: region for region, countries in _BY_REGION.items() for country in countries}


def region_of_country(country):
    """The region name for a country as the corpus spells it, or None."""
    return COUNTRY_REGION.get(country)


def author_region(institutions, countries, institution_countries):
    """The region of an author, or None when nothing says.

    institutions is the author's list of {"name", "first_year", "last_year"};
    the most recent one the country map knows decides (an undated affiliation
    only when nothing else is known). With no institution the country map
    knows, a single country on the author's own list is used; two or more
    would be a guess, so the author has no region.
    """
    best = None
    for inst in institutions or []:
        country = institution_countries.get(inst.get("name"))
        if not country or not region_of_country(country):
            continue
        key = (inst.get("last_year") is not None, inst.get("last_year") or 0,
               inst.get("first_year") or 0)
        if best is None or key > best[0]:
            best = (key, country)
    if best:
        return region_of_country(best[1])
    if len(countries or []) == 1:
        return region_of_country(countries[0])
    return None
