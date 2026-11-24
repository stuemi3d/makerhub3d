"""Saved calculations of an article (issue #161, part b): saving, listing, copying, deleting.

A calculation keeps everything as values (plan, section 4): the spool prices, the rates and the
result. The pages that show it never ask Bambuddy. Every change is written to the change log
by the caller's transaction through the functions here.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit, auth, filament_stock, filaments, stock, table_sort
from app.calc_form import Draft, FilamentPrices, Plan, Row, cents
from app.models import (
    Article,
    Calculation,
    CalculationMaterial,
    CalculationSpool,
    Filament,
    SpoolAssignment,
)
from app.production_form import input_text

SORT_KEYS = (
    "number",
    "name",
    "date",
    "printer",
    "material",
    "power",
    "machine",
    "labour",
    "cost",
    "margin",
    "price",
    "profit",
)
DEFAULT_SORT = ("date", "desc")


def subject(article: Article, calculation: Calculation, language: str) -> str:
    """The text of the change log: article and date of the calculation."""
    return (
        f"{article.number} · {article.name} · {stock.format_date(calculation.created_on, language)}"
    )


def load(db: Session, article_id: int, calculation_id: int) -> Calculation | None:
    """The calculation, only when it belongs to the article."""
    found = db.get(Calculation, calculation_id)
    return found if found is not None and found.article_id == article_id else None


def count(db: Session, article_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Calculation)
            .where(Calculation.article_id == article_id)
        )
    )


def _fill(item: Calculation, plan: Plan) -> None:
    result = plan.result
    item.printer_id = plan.printer_id
    item.printer_name = plan.printer_name
    item.print_minutes = plan.print_minutes
    item.pieces = plan.pieces
    item.setup_minutes = plan.setup_minutes
    item.rework_minutes = plan.rework_minutes
    item.scrap_percent = plan.scrap_percent
    item.margin_percent = plan.margin_percent
    item.power_price = plan.power_price
    item.machine_rate = plan.machine_rate
    item.labour_rate = plan.labour_rate
    item.rounding = plan.rounding
    item.target_price = plan.target_price
    item.material_per_plate = result.material_per_plate
    item.power_per_plate = result.power_per_plate
    item.machine_per_plate = result.machine_per_plate
    item.labour_cost = result.labour_cost
    item.cost_without_labour = result.cost_without_labour
    item.cost_with_labour = result.cost_with_labour
    item.suggested_price = result.suggested_price
    item.selling_price = result.selling_price
    item.profit = result.profit
    item.hourly_wage = result.hourly_wage
    item.materials = [
        CalculationMaterial(
            position=position,
            material=material.material,
            grams=material.grams,
            minutes=material.minutes,
            kwh_per_hour=material.kwh_per_hour,
            price_rule=material.rule,
            filament_id=material.filament_id,
            spools=[
                CalculationSpool(
                    position=slot,
                    spool_id=piece.spool_id,
                    label=piece.label,
                    grams=piece.grams,
                    price_per_kg=piece.price_per_kg,
                )
                for slot, piece in enumerate(material.spools)
            ],
        )
        for position, material in enumerate(plan.materials)
    ]


FILES_KEY = "calculation_files_to_delete"


def _queue_file(db: Session, item: Calculation) -> None:
    """Remembers the stored file of a deleted calculation; the app removes it from the disk
    when the transaction is committed (a rollback forgets it)."""
    if item.file_stored:
        db.info.setdefault(FILES_KEY, []).append(item.file_stored)


def create(
    db: Session,
    actor_id: int,
    article: Article,
    plan: Plan,
    language: str,
    file: tuple[str, str] | None = None,
) -> Calculation:
    """Saves a new calculation; the first one of an article counts as the main one. `file` is
    the shown name and the generated name of its stored file, if it has one."""
    item = Calculation(
        article_id=article.id,
        created_on=date.today(),
        created_at=auth.utcnow(),
        kind="file" if file else "hand",
        file_name=file[0] if file else None,
        file_stored=file[1] if file else None,
        is_main=count(db, article.id) == 0,
        user_id=actor_id,
    )
    _fill(item, plan)
    db.add(item)
    db.flush()
    audit.record_change(db, actor_id, "calculation.created", subject(article, item, language))
    return item


def update(
    db: Session, actor_id: int, article: Article, item: Calculation, plan: Plan, language: str
) -> None:
    """Saves over the calculation; date and the main mark stay."""
    _fill(item, plan)
    db.flush()
    audit.record_change(db, actor_id, "calculation.updated", subject(article, item, language))


def make_main(
    db: Session, actor_id: int, article: Article, item: Calculation, language: str
) -> None:
    for other in db.scalars(select(Calculation).where(Calculation.article_id == article.id)):
        other.is_main = other.id == item.id
    audit.record_change(db, actor_id, "calculation.main", subject(article, item, language))


def remove(db: Session, actor_id: int, article: Article, item: Calculation, language: str) -> None:
    text = subject(article, item, language)
    _queue_file(db, item)
    db.delete(item)
    audit.record_change(db, actor_id, "calculation.deleted", text)


def delete_for_article(db: Session, article_id: int) -> int:
    """Deletes all calculations of an article (when the article goes); returns how many."""
    items = list(db.scalars(select(Calculation).where(Calculation.article_id == article_id)))
    for item in items:
        _queue_file(db, item)
        db.delete(item)
    db.flush()
    return len(items)


@dataclass(frozen=True)
class ListRow:
    """One row of the list: texts to show and the real values to sort by. The money columns
    are per piece, with the share of the scrap, so they add up to the cost."""

    id: int
    article_id: int
    number: str
    name: str
    day: date
    printer: str
    texts: dict[str, str]
    values: dict[str, Decimal]
    is_main: bool

    @property
    def date(self) -> str:
        return self.texts["date"]


def parts_per_piece(item: Calculation) -> dict[str, Decimal]:
    """Material, power and machine of one piece including the scrap, labour per piece."""
    scrap = 1 + item.scrap_percent / 100
    return {
        "material": item.material_per_plate / item.pieces * scrap,
        "power": item.power_per_plate / item.pieces * scrap,
        "machine": item.machine_per_plate / item.pieces * scrap,
        "labour": item.labour_cost,
    }


def rounded_parts(parts: dict[str, Decimal], total: Decimal) -> dict[str, Decimal]:
    """The parts rounded to the cent so that they add up to the rounded total exactly: the
    part that lost the most by rounding down gets the missing cent (largest remainder)."""
    cent = Decimal("0.01")
    exact = {key: value.quantize(cent, rounding=ROUND_FLOOR) for key, value in parts.items()}
    missing = int((total.quantize(cent, rounding=ROUND_HALF_UP) - sum(exact.values())) / cent)
    by_loss = sorted(parts, key=lambda key: exact[key] - parts[key])
    for key in by_loss[: max(missing, 0)]:
        exact[key] += cent
    return exact


def rows(db: Session, language: str, article_id: int | None = None) -> list[ListRow]:
    """All calculations, or only those of one article."""
    query = select(Calculation, Article).join(Article, Article.id == Calculation.article_id)
    if article_id is not None:
        query = query.where(Calculation.article_id == article_id)
    found = []
    for item, article in db.execute(query):
        parts = parts_per_piece(item)
        money = {
            **rounded_parts(parts, item.cost_with_labour),
            "cost": item.cost_with_labour,
            "price": item.suggested_price,
            "profit": item.profit,
        }
        found.append(
            ListRow(
                id=item.id,
                article_id=article.id,
                number=article.number,
                name=article.name,
                day=item.created_on,
                printer=item.printer_name,
                texts={
                    "date": stock.format_date(item.created_on, language),
                    "margin": f"{input_text(item.margin_percent, language)} %",
                    **{key: cents(value, language) for key, value in money.items()},
                },
                values={**money, "margin": item.margin_percent},
                is_main=item.is_main,
            )
        )
    return found


def arranged(
    shown: list[ListRow],
    path: str,
    sort: str | None,
    direction: str | None,
    keep: dict[str, str] | None = None,
) -> tuple[list[ListRow], dict[str, dict[str, str]]]:
    """The rows in the order asked for in the address (default: newest first) and the header
    links of the table."""
    chosen = table_sort.chosen_sort(SORT_KEYS, sort, direction)
    key, order = chosen or DEFAULT_SORT
    shown = sorted(shown, key=lambda row: (row.day, row.id), reverse=True)
    column = {
        "number": lambda row: row.number.casefold(),
        "name": lambda row: row.name.casefold(),
        "date": lambda row: (row.day, row.id),
        "printer": lambda row: row.printer.casefold(),
    }.get(key) or (lambda row: row.values[key])
    shown = sorted(shown, key=column, reverse=order == "desc")
    return shown, table_sort.sort_links(path, SORT_KEYS, chosen, DEFAULT_SORT, keep)


def draft_of(item: Calculation, language: str) -> Draft:
    """The form values of a stored calculation, to copy or edit it. The spools are asked for
    again (with their current prices), so every spool of the position is allowed."""
    return Draft(
        printer=str(item.printer_id) if item.printer_id is not None else "",
        print_minutes=input_text(item.print_minutes, language),
        pieces=str(item.pieces),
        rows=[
            Row(
                material=material.material,
                grams=input_text(material.grams, language),
                minutes=input_text(material.minutes, language),
                spools=[
                    str(piece.spool_id) for piece in material.spools if piece.spool_id is not None
                ],
                rule=material.price_rule,
                filament=str(material.filament_id) if material.filament_id is not None else "",
            )
            for material in item.materials
        ]
        or [Row()],
        setup_minutes=_optional(item.setup_minutes, language),
        rework_minutes=_optional(item.rework_minutes, language),
        scrap=input_text(item.scrap_percent, language),
        margin=input_text(item.margin_percent, language),
        all_spools=True,
        file_name=item.file_name or "",
    )


def _optional(value: Decimal, language: str) -> str:
    return "" if value == 0 else input_text(value, language)


@dataclass(frozen=True)
class DetailSpool:
    label: str
    grams: str
    price: str


@dataclass(frozen=True)
class DetailMaterial:
    material: str
    grams: str
    minutes: str
    kwh: str
    spools: list[DetailSpool]
    # average, last, standard or spool
    rule: str = "spool"


def detail_materials(item: Calculation, language: str) -> list[DetailMaterial]:
    return [
        DetailMaterial(
            material=material.material,
            grams=input_text(material.grams, language),
            minutes=input_text(material.minutes, language),
            kwh=input_text(material.kwh_per_hour, language),
            spools=[
                DetailSpool(
                    piece.label,
                    input_text(piece.grams, language),
                    cents(piece.price_per_kg, language),
                )
                for piece in material.spools
            ],
            rule=material.price_rule,
        )
        for material in item.materials
    ]


def filament_prices_of(db: Session) -> list[FilamentPrices]:
    """The filaments with the prices of the rules, from the values stored with the assignments
    (so archived and deleted spools count and Bambuddy is not asked)."""
    held: dict[int, list[filament_stock.Held]] = {}
    for row in db.scalars(select(SpoolAssignment)):
        held.setdefault(row.filament_id, []).append(
            filament_stock.Held(row.spool_id, row.price_per_kg, row.weight_g, row.assigned_at)
        )
    found = []
    for item in db.scalars(select(Filament)):
        summary = filament_stock.summarize(held.get(item.id, []), {}, None)
        found.append(
            FilamentPrices(
                id=item.id,
                name=filaments.display_name(
                    item.brand, item.material, item.subtype, item.color_name
                ),
                material=item.material,
                active=item.active,
                standard=item.standard_price,
                average=summary.average_price,
                last=summary.last_price,
            )
        )
    return found
