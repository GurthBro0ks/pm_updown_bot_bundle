"""
IBKR ForecastTrader Integration
Scans ForecastEx event contracts and trades via Interactive Brokers.

⚠️ WARNING: This module is DISABLED by default.
Set IBKR_ENABLED=true in .env to enable.
"""

import logging
import os
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

# IBKR Connection Settings
IBKR_ENABLED = os.getenv("IBKR_ENABLED", "false").lower() == "true"
IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.getenv("IBKR_PORT", "7497"))
IBKR_CLIENT_ID = int(os.getenv("IBKR_CLIENT_ID", "10"))

# ForecastEx API (event contracts)
FORECASTEX_API_URL = "https://forecastex.com/api/v1"

# Logging state on load
if not IBKR_ENABLED:
    logger.info("[IBKR] ForecastTrader integration is DISABLED (IBKR_ENABLED=false)")
    logger.info("[IBKR] Set IBKR_ENABLED=true in .env to enable")
else:
    logger.info(f"[IBKR] ForecastTrader enabled - connecting to {IBKR_HOST}:{IBKR_PORT}")

# ============================================================================
# Graceful Import Handling
# ============================================================================

IB_INSYNC_AVAILABLE = False
ib = None

try:
    from ib_insync import IB
    IB_INSYNC_AVAILABLE = True
    logger.info("[IBKR] ib_insync module loaded successfully")
except ImportError as e:
    logger.warning(f"[IBKR] ib_insync not installed: {e}")
    logger.warning("[IBKR] Install with: pip install ib_insync")
    IB = None


# ============================================================================
# ForecastEx Event Scanning
# ============================================================================

def fetch_forecast_ex_events() -> List[Dict]:
    """
    Fetch active ForecastEx event contracts.
    
    ForecastEx provides prediction markets on events (elections, sports, etc.)
    This scans for trading opportunities.
    
    Returns:
        List of event dicts with keys: event_id, title, expiry, implied_prob
    """
    events = []
    
    # Sample event structure (ForecastEx API endpoint would be called here)
    # For now, this is a placeholder - actual API integration would go here
    sample_events = [
        {"event_id": "EVT_001", "title": "Fed Rate Cut 2026", "expiry": "2026-12-31", "implied_prob": 0.65},
        {"event_id": "EVT_002", "title": "Election 2026 Senate", "expiry": "2026-11-03", "implied_prob": 0.52},
    ]
    
    logger.debug(f"[IBKR] Scanned {len(sample_events)} ForecastEx events")
    return sample_events


def analyze_event_opportunity(event: Dict) -> Optional[Dict]:
    """
    Analyze if an event contract presents a trading opportunity.
    
    Args:
        event: Event dict from fetch_forecast_ex_events
        
    Returns:
        Signal dict if opportunity found, None otherwise
    """
    # Placeholder logic - would include actual analysis
    # e.g., compare implied_prob to model predictions
    
    title = event.get("title", "")
    implied_prob = event.get("implied_prob", 0.5)
    
    logger.debug(f"[IBKR] Analyzing: {title} (implied: {implied_prob})")
    
    # Return None - no live trading in this module
    return None


# ============================================================================
# IBKR Connection Management
# ============================================================================

def connect_ibkr() -> Optional[object]:
    """
    Establish connection to Interactive Brokers TWS/Gateway.
    
    Returns:
        IB instance if connected, None otherwise
    """
    if not IB_INSYNC_AVAILABLE:
        logger.error("[IBKR] Cannot connect - ib_insync not installed")
        return None
    
    if not IBKR_ENABLED:
        logger.warning("[IBKR] Cannot connect - IBKR_ENABLED=false")
        return None
    
    try:
        ib = IB()
        ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID)
        logger.info(f"[IBKR] Connected to TWS at {IBKR_HOST}:{IBKR_PORT}")
        return ib
    except Exception as e:
        logger.error(f"[IBKR] Connection failed: {e}")
        return None


def disconnect_ibkr(ib: object) -> None:
    """Disconnect from IBKR TWS/Gateway."""
    if ib and ib.isConnected():
        try:
            ib.disconnect()
            logger.info("[IBKR] Disconnected from TWS")
        except Exception as e:
            logger.warning(f"[IBKR] Disconnect error: {e}")


# ============================================================================
# Main Execution Function
# ============================================================================

def run_ibkr_forecast(mode: str = "shadow", **kwargs) -> bool:
    """
    Main entry point for IBKR ForecastTrader phase.
    
    Args:
        mode: Execution mode (shadow, micro-live, real-live)
        
    Returns:
        True if execution successful, False otherwise
    """
    # Check if enabled
    if not IBKR_ENABLED:
        logger.info("[IBKR] Phase 5 (IBKR ForecastTrader) - DISABLED - skipping")
        return True  # Return True so it doesn't fail the run
    
    # Check if ib_insync is available
    if not IB_INSYNC_AVAILABLE:
        logger.error("[IBKR] ib_insync not installed - cannot run")
        logger.error("[IBKR] Install with: pip install ib_insync")
        return False
    
    logger.info("[IBKR] Starting Phase 5: IBKR ForecastTrader")
    logger.info(f"[IBKR] Mode: {mode}")
    
    # In shadow mode, we just scan and log (no real trades)
    if mode == "shadow":
        logger.info("[IBKR] Running in SHADOW mode - no live trades")
    
    ib = None
    try:
        # Connect to IBKR
        ib = connect_ibkr()
        
        if ib is None:
            logger.warning("[IBKR] Could not connect to TWS - skipping cycle")
            return True  # Don't fail the run
        
        # Fetch ForecastEx events
        events = fetch_forecast_ex_events()
        logger.info(f"[IBKR] Found {len(events)} ForecastEx events")
        
        # Analyze each event
        signals = []
        for event in events:
            signal = analyze_event_opportunity(event)
            if signal:
                signals.append(signal)
        
        logger.info(f"[IBKR] Generated {len(signals)} trading signals")
        
        # In shadow mode, log what we would trade
        if mode == "shadow" and signals:
            for sig in signals:
                logger.info(f"[IBKR] SHADOW: Would trade {sig}")
        
        # Note: Actual order execution would go here
        # For now, this is a placeholder
        
        return True
        
    except Exception as e:
        logger.error(f"[IBKR] Error in run_ibkr_forecast: {e}", exc_info=True)
        return False
        
    finally:
        if ib:
            disconnect_ibkr(ib)


# ============================================================================
# Module Info
# ============================================================================

def get_status() -> Dict:
    """Get module status for diagnostics."""
    return {
        "enabled": IBKR_ENABLED,
        "host": IBKR_HOST,
        "port": IBKR_PORT,
        "client_id": IBKR_CLIENT_ID,
        "ib_insync_available": IB_INSYNC_AVAILABLE,
        "connected": False  # Would check actual connection status
    }
