"""Maps a CLI slug to its scraper class. Add one line per new agency."""

from .agencies.lancaster_county_commissioners import LancasterCountyCommissioners
from .agencies.lincoln_city_council import LincolnCityCouncil
from .agencies.lps_board_of_education import LpsBoardOfEducation
from .agencies.omaha_port_authority import OmahaPortAuthority
from .agencies.oppd_board_of_directors import OppdBoardOfDirectors
from .agencies.ops_board_of_education import OpsBoardOfEducation
from .agencies.planning_commission import PlanningCommission
from .agencies.sarpy_county_commissioners import SarpyCountyCommissioners
from .base import BaseScraper

SCRAPERS: dict[str, type[BaseScraper]] = {
    LancasterCountyCommissioners.slug: LancasterCountyCommissioners,
    LincolnCityCouncil.slug: LincolnCityCouncil,
    LpsBoardOfEducation.slug: LpsBoardOfEducation,
    OmahaPortAuthority.slug: OmahaPortAuthority,
    OppdBoardOfDirectors.slug: OppdBoardOfDirectors,
    OpsBoardOfEducation.slug: OpsBoardOfEducation,
    PlanningCommission.slug: PlanningCommission,
    SarpyCountyCommissioners.slug: SarpyCountyCommissioners,
}


def get(slug: str) -> type[BaseScraper]:
    try:
        return SCRAPERS[slug]
    except KeyError:
        raise KeyError(
            f"Unknown scraper {slug!r}. Known: {', '.join(sorted(SCRAPERS))}"
        ) from None
