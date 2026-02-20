#!/usr/bin/env python3
"""
Simple Logging Configuration
No syntax errors - works with all Python versions
"""

import logging
import sys
from pathlib import Path

def setup_logging(log_file_path=None, verbose=False):
    """Setup logging without syntax errors"""
    
    if verbose:
        level = logging.DEBUG
        format_str = '%(asctime)s | %(levelname)s | %(message)s'
        handlers = [logging.StreamHandler()]
    else:
        level = logging.INFO
        format_str = '%(asctime)s | %(levelname)s | %(message)s'
        handlers = [logging.StreamHandler()]
    
    # Add file handler if path provided
    if log_file_path:
        file_handler = logging.FileHandler(log_file_path)
        handlers.append(file_handler)
    
    logging.basicConfig(
        level=level,
        format=format_str,
        handlers=handlers
    )
    
    return logging.getLogger(__name__)

# Export setup function
__all__ = ['setup_logging']
