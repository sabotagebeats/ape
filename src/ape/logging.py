# Inspired / borrowed from the `click-logging` python package.
import logging
import sys
import traceback
from enum import IntEnum
from pathlib import Path
from typing import IO, Any, Dict, Optional, Union

import click


class LogLevel(IntEnum):
    ERROR = logging.ERROR
    WARNING = logging.WARNING
    SUCCESS = logging.INFO + 1
    INFO = logging.INFO
    DEBUG = logging.DEBUG


logging.addLevelName(LogLevel.SUCCESS.value, LogLevel.SUCCESS.name)
logging.SUCCESS = LogLevel.SUCCESS.value  # type: ignore
DEFAULT_LOG_LEVEL = LogLevel.INFO.name
DEFAULT_LOG_FORMAT = "%(levelname)s%(plugin)s: %(message)s"


def success(self, message, *args, **kws):
    """This method gets injected into python's `logging` module
    to handle logging at this level."""

    if self.isEnabledFor(LogLevel.SUCCESS.value):
        # Yes, logger takes its '*args' as 'args'.
        self._log(LogLevel.SUCCESS.value, message, args, **kws)


logging.Logger.success = success  # type: ignore


CLICK_STYLE_KWARGS = {
    LogLevel.ERROR: dict(fg="bright_red"),
    LogLevel.WARNING: dict(fg="bright_yellow"),
    LogLevel.SUCCESS: dict(fg="bright_green"),
    LogLevel.INFO: dict(fg="blue"),
    LogLevel.DEBUG: dict(fg="blue"),
}
CLICK_ECHO_KWARGS = {
    LogLevel.ERROR: dict(err=True),
    LogLevel.WARNING: dict(err=True),
    LogLevel.SUCCESS: dict(),
    LogLevel.INFO: dict(),
    LogLevel.DEBUG: dict(),
}


# Borrowed from `click._compat`.
def _isatty(stream: IO) -> bool:
    """Returns ``True`` if the stream is part of a tty.
    Borrowed from ``click._compat``."""
    # noinspection PyBroadException
    try:
        return stream.isatty()
    except Exception:
        return False


class ApeColorFormatter(logging.Formatter):
    def __init__(self, fmt: Optional[str] = None):
        fmt = fmt or DEFAULT_LOG_FORMAT
        super().__init__(fmt=fmt)

    def format(self, record):
        if _isatty(sys.stdout) and _isatty(sys.stderr):
            # Only color log messages when sys.stdout and sys.stderr are sent to the terminal.
            level = LogLevel(record.levelno)
            default_dict: Dict[str, Any] = {}
            styles: Dict[str, Any] = CLICK_STYLE_KWARGS.get(level, default_dict)
            record.levelname = click.style(record.levelname, **styles)

        path = Path(record.pathname)
        record.plugin = ""
        for part in path.parts:
            if part.startswith("ape-"):
                record.plugin = f" ({part})"
                break

        return super().format(record)


class ClickHandler(logging.Handler):
    def __init__(self, echo_kwargs):
        super().__init__()
        self.echo_kwargs = echo_kwargs

    def emit(self, record):
        try:
            msg = self.format(record)
            level = record.levelname.lower()
            if self.echo_kwargs.get(level):
                click.echo(msg, **self.echo_kwargs[level])
            else:
                click.echo(msg)
        except Exception:
            self.handleError(record)


class ApeLogger:
    _mentioned_verbosity_option = False
    _extra_loggers: Dict[str, logging.Logger] = {}

    def __init__(
        self,
        _logger: logging.Logger,
        fmt: str,
    ):
        self.error = _logger.error
        self.warning = _logger.warning
        self.success = getattr(_logger, "success", _logger.info)
        self.info = _logger.info
        self.debug = _logger.debug
        self._logger = _logger
        self._load_from_sys_argv()
        self.fmt = fmt

    @classmethod
    def create(cls, fmt: Optional[str] = None) -> "ApeLogger":
        fmt = fmt or DEFAULT_LOG_FORMAT
        _logger = get_logger("ape", fmt=fmt)
        return cls(_logger, fmt)

    def format(self, fmt: Optional[str] = None):
        self.fmt = fmt or DEFAULT_LOG_FORMAT
        fmt = fmt or DEFAULT_LOG_FORMAT
        _format_logger(self._logger, fmt)

    def _load_from_sys_argv(self, default: Optional[Union[str, int]] = None):
        """
        Load from sys.argv to beat race condition with `click`.
        """

        log_level = _get_level(level=default)
        level_names = [lvl.name for lvl in LogLevel]
        for arg_i in range(len(sys.argv) - 1):
            if sys.argv[arg_i] == "-v" or sys.argv[arg_i] == "--verbosity":
                level = _get_level(sys.argv[arg_i + 1].upper())

                if level in level_names:
                    log_level = level
                    break
                else:
                    names_str = f"{', '.join(level_names[:-1])}, or {level_names[-1]}"
                    self._logger.error(f"Must be one of '{names_str}', not '{level}'.")
                    sys.exit(2)

        self.set_level(log_level)

    @property
    def level(self) -> int:
        return self._logger.level

    def set_level(self, level: Union[str, int]):
        """
        Change the global ape logger log-level.

        Args:
            level (str): The name of the level or the value of the log-level.
        """

        if level == self._logger.level:
            return

        self._logger.setLevel(level)
        for _logger in self._extra_loggers.values():
            _logger.setLevel(level)

    def log_error(self, err: Exception):
        """
        Avoids logging empty messages.
        """
        message = str(err)
        if message:
            self._logger.error(message)

    def warn_from_exception(self, err: Exception, message: str):
        """
        Warn the user with the given message,
        log the stack-trace of the error at the DEBUG level, and
        mention how to enable DEBUG logging (only once).
        """
        message = self._create_message_from_error(err, message)
        self._logger.warning(message)
        self.log_debug_stack_trace()

    def error_from_exception(self, err: Exception, message: str):
        """
        Log an error to the user with the given message,
        log the stack-trace of the error at the DEBUG level, and
        mention how to enable DEBUG logging (only once).
        """
        message = self._create_message_from_error(err, message)
        self._logger.error(message)
        self.log_debug_stack_trace()

    def _create_message_from_error(self, err: Exception, message: str):
        err_output = f"{type(err).__name__}: {err}"
        message = f"{message}\n\t{err_output}"
        if not self._mentioned_verbosity_option:
            message += "\n\t(Use `--verbosity DEBUG` to see full stack-trace)"
            self._mentioned_verbosity_option = True

        return message

    def log_debug_stack_trace(self):
        stack_trace = traceback.format_exc()
        self._logger.debug(stack_trace)

    def create_logger(self, new_name: str) -> logging.Logger:
        _logger = get_logger(new_name, self.fmt)
        _logger.setLevel(self.level)
        self._extra_loggers[new_name] = _logger
        return _logger


def _format_logger(_logger: logging.Logger, fmt: str):
    handler = ClickHandler(echo_kwargs=CLICK_ECHO_KWARGS)
    formatter = ApeColorFormatter(fmt=fmt)
    handler.setFormatter(formatter)

    # Remove existing handler(s)
    for existing_handler in _logger.handlers[:]:
        if isinstance(existing_handler, ClickHandler):
            _logger.removeHandler(existing_handler)

    _logger.addHandler(handler)


def get_logger(name: str, fmt: Optional[str] = None) -> logging.Logger:
    """
    Get a logger with the given ``name`` and configure it for usage with Ape.

    Args:
        name (str): The name of the logger.
        fmt (Optional[str]): The format of the logger. Defaults to the Ape
          logger's default format: ``"%(levelname)s%(plugin)s: %(message)s"``.

    Returns:
        ``logging.Logger``
    """
    _logger = logging.getLogger(name)
    _format_logger(_logger, fmt=fmt or DEFAULT_LOG_FORMAT)
    return _logger


def _get_level(level: Optional[Union[str, int]] = None) -> str:
    if level is None:
        return DEFAULT_LOG_LEVEL
    elif isinstance(level, int) or level.isnumeric():
        return LogLevel(int(level)).name

    return level


logger = ApeLogger.create()

# TODO: Can remove this type alias after 0.7
CliLogger = ApeLogger


__all__ = ["DEFAULT_LOG_LEVEL", "logger", "LogLevel", "ApeLogger"]
