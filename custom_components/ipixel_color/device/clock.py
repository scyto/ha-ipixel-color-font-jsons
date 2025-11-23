"""Clock mode commands for iPIXEL Color devices using pypixelcolor."""
from __future__ import annotations

try:
    from pypixelcolor.commands.set_clock_mode import set_clock_mode
except ImportError:
    # Fallback if pypixelcolor is not installed yet
    set_clock_mode = None


def make_clock_mode_command(
    style: int = 1,
    date: str = "",
    show_date: bool = True,
    format_24: bool = True
) -> bytes:
    """Build clock mode command using pypixelcolor.

    Sets the device to clock display mode with customizable style and format.

    Args:
        style: Clock style (0-8). Different visual styles for the clock display.
        date: Date to display in DD/MM/YYYY format. Defaults to today's date.
        show_date: Whether to show the date alongside the time.
        format_24: Whether to use 24-hour format (True) or 12-hour format (False).

    Returns:
        Command bytes for clock mode.

    Raises:
        ValueError: If parameters are out of valid ranges.
        ImportError: If pypixelcolor is not available.
    """
    if set_clock_mode is None:
        raise ImportError("pypixelcolor library is not installed")

    # Call pypixelcolor's set_clock_mode function
    # It returns a SendPlan object with windows containing the command data
    send_plan = set_clock_mode(
        style=style,
        date=date,
        show_date=show_date,
        format_24=format_24
    )

    # Extract the command bytes from the first (and only) window
    if send_plan.windows and len(list(send_plan.windows)) > 0:
        first_window = next(iter(send_plan.windows))
        return first_window.data
    else:
        raise ValueError("pypixelcolor returned empty SendPlan")
