# # -*- coding: utf-8 -*-
# """Main module for monolithic manufacturing application."""
# import os
# import logging.config
#
# # Create log folder for log filename
# LOG_FILENAME = os.getenv('LOG_FILENAME', './volume/monolithic.log')
# os.makedirs(os.path.dirname(LOG_FILENAME), exist_ok=True)
#
# # Configure logger
# LOG_CONFIG_FILENAME = os.getenv('LOG_CONFIG_FILENAME', "logging.conf")
# logging.config.fileConfig(LOG_CONFIG_FILENAME)
# logger = logging.getLogger(__name__)
# logger.debug("Logging configuration set.\n\n")
