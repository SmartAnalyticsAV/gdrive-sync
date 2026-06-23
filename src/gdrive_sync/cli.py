"""CLI gdrive-sync (Typer): auth · sync · status."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import typer

from . import __version__, auth, config
from .drive import DriveClient
from .manifest import Manifest
from .sync import run_sync

app = typer.Typer(
    help="Одностороннее зеркало локальных папок в Google Drive.",
    no_args_is_help=True,
    add_completion=False,
)

_DEFAULT_CONFIG = Path("config.toml")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )


def _fail(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


@app.command("auth")
def auth_cmd(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Подробный лог."),
) -> None:
    """Пройти OAuth-авторизацию и закэшировать токен."""
    _setup_logging(verbose)
    try:
        auth.get_credentials(interactive=True)
    except auth.AuthError as exc:
        _fail(str(exc))
    typer.secho(f"Авторизация успешна. Токен: {config.token_path()}", fg=typer.colors.GREEN)


@app.command()
def sync(
    config_path: Path = typer.Option(
        _DEFAULT_CONFIG, "--config", "-c", help="Путь к TOML-конфигу."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Показать план без изменений на Drive."
    ),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Не открывать браузер (для cron): только молчаливое обновление токена.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Подробный лог."),
) -> None:
    """Синхронизировать локальные папки на Google Drive."""
    _setup_logging(verbose)

    try:
        cfg = config.load_config(config_path)
    except config.ConfigError as exc:
        _fail(f"Конфиг: {exc}")

    if dry_run:
        # dry-run работает офлайн по манифесту — Drive не трогаем.
        with Manifest(config.manifest_path()) as manifest:
            stats = run_sync(cfg, drive=None, manifest=manifest, dry_run=True)  # type: ignore[arg-type]
        _print_stats(stats, dry_run=True)
        return

    try:
        creds = auth.get_credentials(interactive=not non_interactive)
    except auth.AuthError as exc:
        _fail(str(exc))

    drive = DriveClient(auth.build_service(creds))
    with Manifest(config.manifest_path()) as manifest:
        stats = run_sync(cfg, drive=drive, manifest=manifest, dry_run=False)
    _print_stats(stats, dry_run=False)


@app.command()
def status(
    config_path: Path = typer.Option(
        _DEFAULT_CONFIG, "--config", "-c", help="Путь к TOML-конфигу."
    ),
) -> None:
    """Показать состояние: конфиг, манифест, наличие токена."""
    typer.echo(f"gdrive-sync {__version__}")
    typer.echo(f"Каталог приложения: {config.app_home()}")
    typer.echo(f"Токен:    {'есть' if config.token_path().is_file() else 'нет'} ({config.token_path()})")

    manifest_file = config.manifest_path()
    if manifest_file.is_file():
        with Manifest(manifest_file) as manifest:
            files, folders = manifest.counts()
        typer.echo(f"Манифест: {files} файлов, {folders} папок ({manifest_file})")
    else:
        typer.echo(f"Манифест: пуст ({manifest_file})")

    try:
        cfg = config.load_config(config_path)
    except config.ConfigError as exc:
        typer.secho(f"Конфиг: {exc}", fg=typer.colors.YELLOW)
        return

    typer.echo(f"Назначение на Drive: {cfg.dest_folder}")
    typer.echo("Источники:")
    for src in cfg.sources:
        mark = "ok" if src.path.is_dir() else "НЕТ"
        typer.echo(f"  [{mark}] {src.path} → {cfg.dest_folder}/{src.name}")


def _print_stats(stats, *, dry_run: bool) -> None:
    prefix = "[dry-run] " if dry_run else ""
    d = stats.as_dict()
    typer.secho(
        f"{prefix}загружено: {d['uploaded']}, обновлено: {d['updated']}, "
        f"пропущено: {d['skipped']}, папок создано: {d['folders_created']}, "
        f"ошибок: {d['errors']}",
        fg=typer.colors.RED if d["errors"] else typer.colors.GREEN,
    )


if __name__ == "__main__":
    app()
