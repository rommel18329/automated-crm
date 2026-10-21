"""Legal calling window (TCPA/FTC: 8am-9pm in the lead's local time).

The lead's time zone comes from the phone's area code. Area codes not listed
here (or not in a continental US zone) fall back to the safest overlap of all
four zones — 11am-9pm Eastern — so an unknown number is never called early or
late. Omissions only make the guard stricter, never looser.
"""
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

OPEN_HOUR = 8     # first allowed local hour (8:00am)
CLOSE_HOUR = 21   # calls must stop by 9:00pm local

_ET, _CT, _MT, _PT = (ZoneInfo(z) for z in (
    "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles"))
_PHOENIX = ZoneInfo("America/Phoenix")

_CODES = {
    _CT: """319 515 563 641 712 217 224 309 312 331 618 630 708 773 779 815 847 872 219
            218 320 507 612 651 763 952 262 414 534 608 715 920 314 417 573 636 660 816 557
            316 620 785 913 402 531 214 281 713 817 832 972 469 945 346 737 512 210 830 361
            409 936 903 254 325 430 682 940 979 405 580 918 539 479 501 870 225 318 337 504
            985 228 601 662 769 205 251 256 334 659 938""",
    _ET: """212 646 718 917 347 929 202 305 786 407 321 689 813 904 386 404 470 678 770
            617 857 215 267 445 412 703 571 410 443 667 313 586 248 734 947 216 440 330 614
            937 513 380 317 463 860 959 203 475 704 980 919 984 336 910""",
    _MT: "303 720 970 801 385 435 505 575 208 406 307",
    _PHOENIX: "602 480 623",
    _PT: """213 310 323 424 818 626 909 714 949 951 619 858 760 442 559 408 669 510 415 628
            650 925 916 530 805 831 206 253 360 425 509 503 971 541 702 725 775""",
}
_ZONE_BY_AREA = {code: tz for tz, codes in _CODES.items() for code in codes.split()}


def _area(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    return digits[:3]


def can_call_now(phone: str, now: datetime | None = None) -> tuple[bool, str]:
    """(allowed, reason). `now` must be timezone-aware (default: current time)."""
    now = now or datetime.now(ZoneInfo("UTC"))
    if now.tzinfo is None:
        raise ValueError("now must include its timezone")
    tz = _ZONE_BY_AREA.get(_area(phone))
    if tz is None:                      # unknown: valid in every zone at once
        local = now.astimezone(_ET)
        ok = OPEN_HOUR + 3 <= local.hour < CLOSE_HOUR
        return ok, ("" if ok else "unknown time zone; outside the safe 11am-9pm Eastern window")
    local = now.astimezone(tz)
    ok = OPEN_HOUR <= local.hour < CLOSE_HOUR
    return ok, ("" if ok else f"{local:%I:%M%p} local time ({tz.key.split('/')[-1]}) is outside 8am-9pm")
