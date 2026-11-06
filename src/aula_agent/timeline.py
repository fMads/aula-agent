"""The overview's timeline (#29, #54): what stands where on the front page, day by day and week by week.
It returns data; web.py renders it.

Gælder nu is what :meth:`archive.Item.current` says is current. I dag og i morgen and Kommende are days, and
Tidligere is weeks (Monday first), each with its lines: an item on its day (:func:`placed_on`), an event in the
calendar or an open todo with due. Standing info is never on the days or in the weeks.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from . import archive
from .ledger import UpcomingEvent

EARLIER_WEEKS = 8  # how many weeks back "Tidligere" shows before "Vis ældre"


def monday(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def placed_on(item: archive.Item) -> dt.date | None:
    """The day an item has on the timeline: when its span begins, or else when it came."""
    c = item.classification
    if c.span_start:
        return c.span_start
    return dt.date.fromisoformat(item.latest.date[:10]) if item.latest.date else None


def in_weeks(items: list[archive.Item]) -> list[tuple[dt.date, archive.Item]]:
    """The items that stand in the weeks (not standing info), each on its day."""
    return [(day, i) for i in items if i.classification.category != archive.Category.INFO and (day := placed_on(i))]


@dataclass(frozen=True)
class ItemLine:
    """An item on its day; ``todo``: its open todo, when it is due that same day (then not a line of its own)."""

    day: dt.date
    item: archive.Item
    todo: archive.ItemTodo | None = None


@dataclass(frozen=True)
class EventLine:
    """An event in the calendar; ``day`` is where it stands (an event begun before today: today)."""

    day: dt.date
    event: UpcomingEvent
    source: archive.Item | None  # the item it came from, when the archive has it

    @property
    def start(self) -> dt.date:
        return dt.date.fromisoformat(self.event.start[:10])


@dataclass(frozen=True)
class TodoLine:
    """An open todo on its due."""

    day: dt.date
    todo: archive.ItemTodo


Line = ItemLine | EventLine | TodoLine
_ORDER = {ItemLine: 0, EventLine: 1, TodoLine: 2}  # on the same day: the item, then the event, then the todo


@dataclass(frozen=True)
class Week:
    monday: dt.date
    lines: tuple[Line, ...]


def weeks(lines: list[Line], *, newest_first: bool = False) -> list[Week]:
    """The lines grouped in their weeks, by day (and then as :data:`_ORDER` has them)."""
    by_week: dict[dt.date, list[Line]] = {}
    for line in sorted(lines, key=lambda line: (line.day, _ORDER[type(line)]), reverse=newest_first):
        by_week.setdefault(monday(line.day), []).append(line)
    return [Week(first, tuple(members)) for first, members in by_week.items()]


def is_upcoming(item: archive.Item, today: dt.date) -> bool:
    """Whether an item stands in Kommende: its span begins today or later, and it is not in Gælder nu."""
    c = item.classification
    return c.category != archive.Category.INFO and bool(c.span_start) and c.span_start >= today and item.current(today) is None


@dataclass(frozen=True)
class Day:
    day: dt.date
    lines: tuple[Line, ...]


@dataclass(frozen=True)
class Upcoming:
    """I dag og i morgen (``near``: today and tomorrow, also when there is nothing on them) and Kommende
    (``later``: from the day after tomorrow, the days with something on them)."""

    near: tuple[Day, Day]
    later: list[Day]


def upcoming(
    items: list[archive.Item], events: list[UpcomingEvent], todos: list[archive.ItemTodo], today: dt.date,
    sources: dict[str, archive.Item],
) -> Upcoming:
    """I dag og i morgen and Kommende, day by day from today: the items :func:`is_upcoming`, the events (an event
    on the day its item's span begins is that item, shown once: here or in Gælder nu) and the open ``todos`` due
    today or later. ``sources``: the events' items in the archive (shown or not), by key."""
    by_key = {i.key: i for i in items}
    due = {t.item.key: t for t in todos if t.due is not None and t.due >= today}
    lines: list[Line] = []
    for item in items:
        if is_upcoming(item, today):
            day = item.classification.span_start
            todo = due.pop(item.key) if item.key in due and due[item.key].due == day else None
            lines.append(ItemLine(day, item, todo))
    for ev in events:
        start = dt.date.fromisoformat(ev.start[:10])
        source = by_key.get(ev.source or "")
        if source is not None and source.classification.span_start == start:
            continue
        lines.append(EventLine(max(start, today), ev, sources.get(ev.source or "")))
    lines += [TodoLine(t.due, t) for t in due.values()]
    by_day: dict[dt.date, list[Line]] = {}
    for line in sorted(lines, key=lambda line: (line.day, _ORDER[type(line)])):
        by_day.setdefault(line.day, []).append(line)
    tomorrow = today + dt.timedelta(days=1)
    near = (Day(today, tuple(by_day.pop(today, ()))), Day(tomorrow, tuple(by_day.pop(tomorrow, ()))))
    return Upcoming(near, [Day(day, tuple(members)) for day, members in by_day.items()])


@dataclass(frozen=True)
class Earlier:
    weeks: list[Week]  # newest first
    older: dt.date | None  # the ``before`` of "Vis ældre", when there is something before these weeks


def earlier(items: list[archive.Item], before: dt.date, today: dt.date) -> Earlier:
    """Tidligere: the days before ``before`` back to the Monday :data:`EARLIER_WEEKS` weeks before its week,
    newest first. With ``before`` today, also today's items that are nowhere else (no span, not current)."""
    start = monday(before) - dt.timedelta(weeks=EARLIER_WEEKS)
    placed = in_weeks(items)

    def shown(day: dt.date, item: archive.Item) -> bool:
        if day == today == before:
            return not item.classification.span_start and item.current(today) is None
        return start <= day < before

    lines = [ItemLine(day, i) for day, i in sorted(placed, key=lambda p: p[1].key, reverse=True) if shown(day, i)]
    return Earlier(weeks(lines, newest_first=True), start if any(day < start for day, _ in placed) else None)


def now(items: list[archive.Item], today: dt.date) -> list[tuple[archive.Item, archive.Current]]:
    """Gælder nu: the items current today (:meth:`archive.Item.current`), with why, the latest placed first."""
    current = [(i, why) for i in items if (why := i.current(today)) is not None]
    current.sort(key=lambda n: (placed_on(n[0]) or dt.date.min, n[0].key), reverse=True)
    return current
