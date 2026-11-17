"""Saved calculations of an article (issue #161, part b): saving, listing, copying, deleting.

A calculation keeps everything as values (plan, section 4): the spool prices, the rates and the
result. The pages that show it never ask Bambuddy. Every change is written to the change log
by the caller's transaction through the functions here.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit, auth, stock, table_sort
from app.calc_form import Draft, Plan, Row, cents
from app.models import Article, Calculation, CalculationMaterial, CalculationSpool
from app.production_form import input_text

SORT_KEYS = ("article", "date", "printer", "cost", "price")
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
    id: int
    article_id: int
    article: str
    date: str
    sort_date: tuple[date, int]
    printer: str
    cost: str
    price: str
    sort_cost: Decimal
    sort_price: Decimal
    is_main: bool


def rows(db: Session, language: str, article_id: int | None = None) -> list[ListRow]:
    """All calculations, or only those of one article."""
    query = select(Calculation, Article).join(Article, Article.id == Calculation.article_id)
    if article_id is not None:
        query = query.where(Calculation.article_id == article_id)
    return [
        ListRow(
            id=item.id,
            article_id=article.id,
            article=f"{article.number} · {article.name}",
            date=stock.format_date(item.created_on, language),
            sort_date=(item.created_on, item.id),
            printer=item.printer_name,
            cost=cents(item.cost_with_labour, language),
            price=cents(item.suggested_price, language),
            sort_cost=item.cost_with_labour,
            sort_price=item.suggested_price,
            is_main=item.is_main,
        )
        for item, article in db.execute(query)
    ]


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
    shown = sorted(shown, key=lambda row: row.sort_date, reverse=True)
    column = {
        "article": lambda row: row.article.casefold(),
        "date": lambda row: row.sort_date,
        "printer": lambda row: row.printer.casefold(),
        "cost": lambda row: row.sort_cost,
        "price": lambda row: row.sort_price,
    }[key]
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
                spools=[str(piece.spool_id) for piece in material.spools],
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
        )
        for material in item.materials
    ]
