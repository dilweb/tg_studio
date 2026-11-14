"""Логи для разбора нагрузочного теста — отдельный файл, без мусора.

Подключается только в docker-compose.loadtest.yml (PYTHONPATH + LOADTEST_SERVICE_LOG):
Python сам импортирует sitecustomize при старте любого процесса (api, celery, мок).
Пишем WARNING и выше от кода приложения (tg_studio.*), необработанные исключения
uvicorn (500 с трейсбеком), падения задач celery и пул соединений SQLAlchemy.
Access-лог, SQL-echo и INFO не пишем.

uvicorn настраивает логирование через dictConfig и при этом снимает чужие
обработчики со своих логгеров — поэтому после каждого dictConfig обработчик
навешивается заново.
"""

import logging
import logging.config
import os

_NAME = os.environ.get("LOADTEST_SERVICE_LOG")
_LOGGERS = ("tg_studio", "uvicorn.error", "celery.app.trace", "celery.worker", "sqlalchemy.pool", "apipay_mock")

if _NAME:
    _dir = os.environ.get("LOADTEST_LOG_DIR", "/reports/service-logs")
    os.makedirs(_dir, exist_ok=True)
    _handler = logging.FileHandler(os.path.join(_dir, f"{_NAME}.log"), encoding="utf-8", delay=True)
    _handler.setLevel(logging.WARNING)
    # время — UTC контейнера (TZ=UTC в compose): скрипт режет файл по началу прогона
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(process)d] %(name)s: %(message)s"))

    def _attach() -> None:
        for name in _LOGGERS:
            logger = logging.getLogger(name)
            if _handler not in logger.handlers:
                logger.addHandler(_handler)

    _orig_dict_config = logging.config.dictConfig

    def _dict_config(config):
        _orig_dict_config(config)
        _attach()

    logging.config.dictConfig = _dict_config
    _attach()
