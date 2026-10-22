"""Configuración común de logs para consola."""

import logging.config


def configure_logging(service_name: str, level: str = "INFO") -> None:
    """Configura el logger app y sus hijos sin desactivar los de otras librerías."""
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "service": {
                "format": (
                    "%(asctime)s [" + service_name + "] "
                    "%(levelname)s %(name)s: %(message)s"
                ),
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "service",
                "level": level,
            },
        },
        "root": {"handlers": ["console"], "level": level},
        "loggers": {
            "app": {"handlers": ["console"], "level": level, "propagate": False},
        },
    })
