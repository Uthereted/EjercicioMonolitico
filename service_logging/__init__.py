"""Manufacturing-specific settings for the chassis logging infrastructure."""
from my_chassis.observability import (
    LogRuntime, LogUnavailable, RabbitLogHandler, LoggedEventBus, UNAVAILABLE, runtime,
    install_logging as _install_logging,
)

LOG_EXCHANGE = 'manufacturing.logs'
runtime.exchange_name = LOG_EXCHANGE


def install_logging(app, service):
    return _install_logging(app, service,
                            health_paths=('/service/health', '/health', '/deliveries/'))
