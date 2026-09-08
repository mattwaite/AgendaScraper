"""Maps a CLI slug to its scraper class. Add one line per new agency."""

from .agencies.lincoln_city_council import LincolnCityCouncil
from .base import BaseScraper

SCRAPERS: dict[str, type[BaseScraper]] = {
    LincolnCityCouncil.slug: LincolnCityCouncil,
}


def get(slug: str) -> type[BaseScraper]:
    try:
        return SCRAPERS[slug]
    except KeyError:
        raise KeyError(
            f"Unknown scraper {slug!r}. Known: {', '.join(sorted(SCRAPERS))}"
        ) from None
