"""My account: set up, turn off and renew recovery codes of the two-factor login (issue #30)."""

from collections.abc import Callable

from fastapi import FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import auth, two_factor
from app.permissions import Permission, requires
from app.two_factor import Cipher


def add_two_factor_routes(
    app: FastAPI,
    new_session: Callable[[], Session],
    cipher: Cipher | None,
    render: Callable[..., HTMLResponse],
    render_account: Callable[..., HTMLResponse],
    read_form: Callable,
    logged_out_response: Callable[[], Response],
) -> None:
    view = [requires(Permission.VIEW)]

    def redirect(url: str) -> RedirectResponse:
        return RedirectResponse(url, status_code=303)

    def render_codes(request: Request, codes: list[str]) -> HTMLResponse:
        # Shown only in this answer; afterwards only fingerprints exist.
        response = render(request, "two_factor_codes.html", "", codes=codes)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/account/2fa/start", response_class=HTMLResponse, dependencies=view)
    async def start_setup(request: Request) -> Response:
        if cipher is None:
            return redirect("/account")
        raw = await read_form(request, ("tfa_password",))
        errors, logged_out = await run_in_threadpool(
            auth.start_two_factor_setup,
            new_session,
            cipher,
            request.state.user.id,
            raw.get("tfa_password", ""),
        )
        if logged_out:
            return logged_out_response()
        if errors:
            return render_account(request, status_code=422, errors=errors)
        return redirect("/account/2fa/setup")

    def render_setup(request: Request, secret: str, status_code: int = 200, **context):
        uri = two_factor.provisioning_uri(secret, request.state.user.username)
        response = render(
            request,
            "two_factor_setup.html",
            "",
            status_code=status_code,
            qr_code=two_factor.qr_code_data_uri(uri),
            secret=two_factor.grouped_secret(secret),
            **context,
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/account/2fa/setup", response_class=HTMLResponse, dependencies=view)
    async def setup_page(request: Request) -> Response:
        secret = None
        if cipher is not None:
            secret = await run_in_threadpool(
                auth.pending_setup_secret, new_session, cipher, request.state.user.id
            )
        if secret is None:
            return redirect("/account")
        return render_setup(request, secret, failed=False)

    @app.post("/account/2fa/confirm", response_class=HTMLResponse, dependencies=view)
    async def confirm_setup(request: Request) -> Response:
        if cipher is None:
            return redirect("/account")
        raw = await read_form(request, ("code",))
        user_id = request.state.user.id
        codes = await run_in_threadpool(
            auth.confirm_two_factor_setup, new_session, cipher, user_id, raw.get("code", "")
        )
        if codes is not None:
            return render_codes(request, codes)
        secret = await run_in_threadpool(auth.pending_setup_secret, new_session, cipher, user_id)
        if secret is None:
            return redirect("/account")
        return render_setup(request, secret, status_code=422, failed=True)

    @app.post("/account/2fa/disable", response_class=HTMLResponse, dependencies=view)
    async def disable(request: Request) -> Response:
        raw = await read_form(request, ("off_password", "off_code"))
        errors, logged_out = await run_in_threadpool(
            auth.disable_two_factor, new_session, cipher, request.state.user.id, raw
        )
        if logged_out:
            return logged_out_response()
        if errors:
            # Neither password nor code is sent back.
            return render_account(request, status_code=422, errors=errors)
        return redirect("/account?changed=2fa_off")

    @app.post("/account/2fa/codes", response_class=HTMLResponse, dependencies=view)
    async def renew_codes(request: Request) -> Response:
        raw = await read_form(request, ("renew_code",))
        codes, errors, logged_out = await run_in_threadpool(
            auth.renew_recovery_codes,
            new_session,
            cipher,
            request.state.user.id,
            raw.get("renew_code", ""),
        )
        if logged_out:
            return logged_out_response()
        if codes is None:
            return render_account(request, status_code=422, errors=errors)
        return render_codes(request, codes)
