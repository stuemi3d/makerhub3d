"""Variants of templates (issue #49): inheriting values and the attribute values of a variant.

A variant is an article that points to a template. Price, weight and minimum stock that are
empty on the variant are taken from the template; an own value wins. Nothing here is
specific to a page, so pages for creating and editing variants share it.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Article,
    ArticleAttributeValue,
    Attribute,
    AttributeValue,
    TemplateAttribute,
)

INHERITED_FIELDS = ("selling_price", "weight", "min_stock")


def effective(article: Article, field: str):
    """The value that counts: the own one, else the template's (for a variant)."""
    own = getattr(article, field)
    if own is None and article.template is not None:
        return getattr(article.template, field)
    return own


def source(article: Article, field: str) -> str | None:
    """ "own", "inherited" or None (nothing set); only meaningful for a variant."""
    if getattr(article, field) is not None:
        return "own"
    if article.template is not None and getattr(article.template, field) is not None:
        return "inherited"
    return None


def template_attributes(db: Session, template_id: int) -> list[Attribute]:
    """The attributes of a template in the order they are shown."""
    rows = db.execute(
        select(Attribute)
        .join(TemplateAttribute, TemplateAttribute.attribute_id == Attribute.id)
        .where(TemplateAttribute.template_id == template_id)
        .order_by(TemplateAttribute.position)
    )
    return [attribute for (attribute,) in rows]


def attribute_fields(db: Session, template_id: int) -> list[dict]:
    """The attributes of a template with their values, as the form needs them."""
    return [
        {
            "id": attribute.id,
            "name": attribute.name,
            "options": [
                {"id": value.id, "value": value.value, "abbreviation": value.abbreviation}
                for value in attribute.values_rel
            ],
        }
        for attribute in template_attributes(db, template_id)
    ]


def field_name(attribute_id: int) -> str:
    return f"attr_{attribute_id}"


def chosen(article: Article) -> dict[int, int]:
    """attribute id -> value id of a variant."""
    return {row.attribute_id: row.value_id for row in article.attribute_values}


def selected_values(article: Article) -> dict[str, str]:
    """The current values as the form fields show them."""
    return {
        field_name(attribute_id): str(value_id)
        for attribute_id, value_id in chosen(article).items()
    }


def attribute_rows(db: Session, article: Article) -> list[dict]:
    """The attribute values of a variant for display; value is None where it is missing."""
    if article.template_id is None:
        return []
    current = {row.attribute_id: row.value.value for row in article.attribute_values}
    return [
        {"name": attribute.name, "value": current.get(attribute.id)}
        for attribute in template_attributes(db, article.template_id)
    ]


def check_choices(
    raw: dict[str, str], fields: list[dict], may_stay_empty: frozenset[int] = frozenset()
) -> tuple[dict[int, int], dict[str, str]]:
    """Reads the selected values. Returns attribute id -> value id and the errors by field.

    Every attribute needs a value from its own list; only an attribute that a variant
    already lacks (it was added to the template later) may stay empty.
    """
    choices: dict[int, int] = {}
    errors: dict[str, str] = {}
    for field in fields:
        name = field_name(field["id"])
        text = raw.get(name, "").strip()
        if not text:
            if field["id"] not in may_stay_empty:
                errors[name] = "variant.error.value_required"
            continue
        allowed = {str(value["id"]) for value in field["options"]}
        if text not in allowed:
            errors[name] = "variant.error.value_invalid"
        else:
            choices[field["id"]] = int(text)
    return choices, errors


def combo_key(choices: dict[int, int]) -> str:
    return "|".join(f"{attribute}:{value}" for attribute, value in sorted(choices.items()))


def combination_taken(
    db: Session, template_id: int, choices: dict[int, int], own_id: int | None = None
) -> bool:
    """True when another variant of the template has exactly these values."""
    query = select(Article.id).where(
        Article.template_id == template_id, Article.combo_key == combo_key(choices)
    )
    if own_id is not None:
        query = query.where(Article.id != own_id)
    return db.scalar(query) is not None


def apply_choices(db: Session, article: Article, choices: dict[int, int]) -> None:
    """Gives the variant exactly these values (changing, adding and removing rows)."""
    rows = {row.attribute_id: row for row in article.attribute_values}
    for attribute_id, row in rows.items():
        if attribute_id not in choices:
            article.attribute_values.remove(row)
    for attribute_id, value_id in choices.items():
        if attribute_id in rows:
            rows[attribute_id].value_id = value_id
        else:
            article.attribute_values.append(
                ArticleAttributeValue(attribute_id=attribute_id, value_id=value_id)
            )
    article.combo_key = combo_key(choices)
    db.flush()
    # Rows changed through the foreign key must show their new value objects.
    db.expire(article, ["attribute_values"])


def describe(db: Session, choices: dict[int, int]) -> dict[int, str]:
    """attribute id -> "Colour: red", for the change log."""
    result = {}
    for attribute_id, value_id in choices.items():
        attribute = db.get(Attribute, attribute_id)
        value = db.get(AttributeValue, value_id)
        result[attribute_id] = f"{attribute.name}: {value.value}"
    return result


def value_changes(old: dict[int, str], new: dict[int, str]) -> list[dict[str, str]]:
    """Change log details for the differences between two sets of described values."""
    return [
        {
            "field": "attribute_value",
            "old": old.get(attribute_id, ""),
            "new": new.get(attribute_id, ""),
        }
        for attribute_id in sorted(set(old) | set(new))
        if old.get(attribute_id, "") != new.get(attribute_id, "")
    ]


def variants_of(db: Session, template_id: int) -> list[Article]:
    return list(
        db.scalars(
            select(Article).where(Article.template_id == template_id).order_by(Article.number_key)
        )
    )
