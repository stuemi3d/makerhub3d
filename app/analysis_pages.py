"""Analysis page of everything with a period (issue #206); everyone who may view can read it."""

from collections.abc import Callable
from datetime import date

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app import analysis_total, stock
from app.analysis import money
from app.permissions import Permission, requires


def add_analysis_routes(
    app: FastAPI,
    new_session: Callable[[], Session],
    render: Callable[..., HTMLResponse],
    language: str,
) -> None:
    @app.get("/analysis", response_class=HTMLResponse, dependencies=[requires(Permission.VIEW)])
    def analysis_page(
        request: Request,
        period: str | None = None,
        start: str = Query("", alias="from"),
        end: str = Query("", alias="to"),
    ) -> HTMLResponse:
        today = date.today()
        chosen = analysis_total.choose(period, start, end, today)
        context: dict[str, object] = {
            "periods": analysis_total.PERIODS,
            "period_choice": chosen,
            "start_text": start[:10],
            "end_text": end[:10],
            "shown": None,
        }
        if chosen.error is None:
            with new_session() as db:
                totals = analysis_total.compute(db, chosen.start, chosen.end, today)
            context.update(
                totals=totals,
                shown=analysis_total.texts(totals, language),
                period_text=(
                    f"{stock.format_date(chosen.start, language)} \u2013 "
                    f"{stock.format_date(chosen.end, language)}"
                ),
                money=lambda value: money(value, language),
                day=lambda value: stock.format_date(value, language),
            )
        return render(request, "analysis_total.html", "nav.analysis", **context)
