"""Target canvas classes, and the catalogue of real formats we test against.

The organizers draw hidden targets from a catalogue of common platform
formats, so this list is our best guess at it. The class drives which
layouts are worth trying; the short side drives the type floors.
"""

from dataclasses import dataclass

# (upper bound on width / height, class name), checked in order
_CLASSES = [
    (0.6, "ultra_tall"),
    (0.9, "tall"),
    (1.15, "square"),
    (2.5, "wide"),
    (5.0, "very_wide"),
    (float("inf"), "ultra_wide"),
]


@dataclass(frozen=True)
class Format:
    name: str
    width: int
    height: int

    @property
    def ratio(self) -> float:
        return self.width / self.height

    @property
    def cls(self) -> str:
        return classify(self.width, self.height)

    @property
    def slug(self) -> str:
        return self.name.lower().replace(" ", "_").replace("/", "_")


def classify(width: float, height: float) -> str:
    r = width / height
    return next(name for bound, name in _CLASSES if r < bound)


CATALOGUE = [
    Format("Instagram Post", 1080, 1080),
    Format("Instagram Portrait", 1080, 1350),
    Format("Instagram Story", 1080, 1920),
    Format("Facebook Post", 1200, 630),
    Format("Facebook Cover", 820, 312),
    Format("X Post", 1600, 900),
    Format("X Header", 1500, 500),
    Format("LinkedIn Post", 1200, 627),
    Format("LinkedIn Banner", 1584, 396),
    Format("YouTube Thumbnail", 1280, 720),
    Format("YouTube Channel Art", 2560, 1440),
    Format("Pinterest Pin", 1000, 1500),
    Format("Presentation 16x9", 1920, 1080),
    Format("Website Banner", 1600, 400),
    Format("Email Header", 600, 200),
    Format("Leaderboard", 728, 90),
    Format("Billboard", 970, 250),
    Format("Banner", 468, 60),
    Format("Mobile Banner", 320, 50),
    Format("Large Mobile Banner", 320, 100),
    Format("Medium Rectangle", 300, 250),
    Format("Large Rectangle", 336, 280),
    Format("Half Page", 300, 600),
    Format("Wide Skyscraper", 160, 600),
    Format("A4 Poster", 2480, 3508),
    Format("US Letter Poster", 2550, 3300),
]
