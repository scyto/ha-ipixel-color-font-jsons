"""iPIXEL Color Bluetooth API client."""
from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any
from zlib import crc32

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

from homeassistant.exceptions import HomeAssistantError

from .const import WRITE_UUID, NOTIFY_UUID, DEVICE_NAME_PREFIX, CONNECTION_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class iPIXELError(HomeAssistantError):
    """Base iPIXEL error."""


class iPIXELConnectionError(iPIXELError):
    """iPIXEL connection error."""


class iPIXELTimeoutError(iPIXELError):
    """iPIXEL timeout error."""


class iPIXELAPI:
    """iPIXEL Color device API client."""

    def __init__(self, address: str) -> None:
        """Initialize the API client."""
        self._address = address
        self._client: BleakClient | None = None
        self._connected = False
        self._power_state = False
        
    async def connect(self) -> bool:
        """Connect to the iPIXEL device."""
        _LOGGER.debug("Connecting to iPIXEL device at %s", self._address)
        
        try:
            self._client = BleakClient(self._address)
            await asyncio.wait_for(
                self._client.connect(), timeout=CONNECTION_TIMEOUT
            )
            self._connected = True
            
            # Enable notifications
            await self._client.start_notify(NOTIFY_UUID, self._notification_handler)
            _LOGGER.info("Successfully connected to iPIXEL device")
            return True
            
        except asyncio.TimeoutError as err:
            _LOGGER.error("Connection timeout to %s: %s", self._address, err)
            raise iPIXELTimeoutError(f"Connection timeout: {err}") from err
        except BleakError as err:
            _LOGGER.error("Failed to connect to %s: %s", self._address, err)
            raise iPIXELConnectionError(f"Connection failed: {err}") from err

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        if self._client and self._connected:
            try:
                await self._client.stop_notify(NOTIFY_UUID)
                await self._client.disconnect()
                _LOGGER.debug("Disconnected from iPIXEL device")
            except BleakError as err:
                _LOGGER.error("Error during disconnect: %s", err)
            finally:
                self._connected = False

    async def _send_command(self, command: bytes) -> bool:
        """Send command to the device."""
        if not self._connected or not self._client:
            raise iPIXELConnectionError("Device not connected")

        try:
            _LOGGER.debug("Sending command: %s", command.hex())
            await self._client.write_gatt_char(WRITE_UUID, command)
            return True
        except BleakError as err:
            _LOGGER.error("Failed to send command: %s", err)
            return False

    async def set_power(self, on: bool) -> bool:
        """Set device power state.
        
        Command format from protocol documentation:
        [5, 0, 7, 1, on_byte] where on_byte = 1 for on, 0 for off
        """
        on_byte = 1 if on else 0
        command = bytes([5, 0, 7, 1, on_byte])
        
        success = await self._send_command(command)
        if success:
            self._power_state = on
            _LOGGER.debug("Power set to %s", "ON" if on else "OFF")
        return success
    
    async def display_text(
        self, 
        text: str,
        effect: str = "fixed",
        speed: int = 50,
        color_fg: tuple[int, int, int] = (255, 255, 255),
        color_bg: tuple[int, int, int] = (0, 0, 0)
    ) -> bool:
        """Display text on the device with various effects.
        
        Args:
            text: Text to display (max 500 characters)
            effect: Display effect - 'fixed', 'scroll_rtl', 'scroll_ltr', 'blink', 'breeze', 'snow', 'laser'
            speed: Animation speed (1-100)
            color_fg: Foreground RGB color tuple
            color_bg: Background RGB color tuple
        """
        # Validate inputs
        if len(text) > 500:
            text = text[:500]
        
        if not 1 <= speed <= 100:
            speed = max(1, min(100, speed))
        
        # Map effect names to protocol values
        effects = {
            "fixed": 0x00,
            "scroll_rtl": 0x01,  # Right to left
            "scroll_ltr": 0x02,  # Left to right
            "blink": 0x05,
            "breeze": 0x06,
            "snow": 0x07,
            "laser": 0x08
        }
        effect_byte = effects.get(effect, 0x00)
        
        # Build text data according to protocol
        text_bytes = text.encode('utf-8')
        text_len = len(text_bytes)
        
        # Build the text data payload
        txt_data = bytearray()
        txt_data.extend(struct.pack('<H', text_len))  # Text length (little-endian)
        txt_data.extend([0x01, 0x01])  # Unknown fixed bytes
        txt_data.append(effect_byte)  # Effect type
        txt_data.append(speed)  # Speed (1-100)
        txt_data.append(0x01)  # Style (fixed for now)
        txt_data.extend(color_fg)  # Foreground RGB
        txt_data.append(0x01)  # Unknown (text direction?)
        txt_data.extend(color_bg)  # Background RGB
        
        # Add character bitmap data for basic ASCII
        # Using 10x16 font size for now
        for char in text:
            txt_data.append(0x80)  # PIX_DATA marker
            txt_data.extend(color_fg)  # Character color
            txt_data.extend([0x0A, 0x10])  # Character size 10x16
            
            # Simple bitmap representation (simplified for now)
            # In production, we'd need proper font bitmaps
            if char == ' ':
                # Space character - all bits off
                for _ in range(16):
                    txt_data.extend([0x00, 0x00])
            else:
                # For other characters, create a simple pattern
                # This is placeholder - real implementation needs font data
                for i in range(16):
                    txt_data.extend([0xFF, 0x03])  # Simple filled rectangle
        
        # Calculate CRC32 of txt_data
        data_crc = crc32(txt_data) & 0xFFFFFFFF
        
        # Build complete command
        command = bytearray()
        # Command header: length(2) + cmd(2)
        total_len = len(txt_data) + 11  # +11 for fixed header bytes
        command.extend(struct.pack('<H', total_len))  # Total length
        command.extend([0x00, 0x01])  # Command 0x0100
        
        # Data section
        command.append(0x00)  # Unknown fixed
        command.extend(struct.pack('<I', len(txt_data)))  # Data size
        command.extend(struct.pack('<I', data_crc))  # CRC32
        command.append(0x00)  # Unknown fixed
        command.append(0x01)  # Screen number (1)
        command.extend(txt_data)  # The actual text data
        
        success = await self._send_command(bytes(command))
        if success:
            _LOGGER.debug("Text displayed: %s with effect %s", text, effect)
        return success

    def _notification_handler(self, sender: Any, data: bytearray) -> None:
        """Handle notifications from the device."""
        _LOGGER.debug("Notification from %s: %s", sender, data.hex())
        # For this basic version, we just log notifications
        # Future versions will parse responses and update state

    @property
    def is_connected(self) -> bool:
        """Return True if connected to device."""
        return self._connected and self._client and self._client.is_connected

    @property
    def power_state(self) -> bool:
        """Return current power state."""
        return self._power_state

    @property
    def address(self) -> str:
        """Return device address."""
        return self._address


async def discover_ipixel_devices(timeout: int = 10) -> list[dict[str, Any]]:
    """Discover iPIXEL devices via Bluetooth scanning."""
    _LOGGER.debug("Starting iPIXEL device discovery")
    devices = []

    def detection_callback(device, advertisement_data):
        """Handle device detection."""
        if device.name and device.name.startswith(DEVICE_NAME_PREFIX):
            device_info = {
                "address": device.address,
                "name": device.name,
                "rssi": advertisement_data.rssi,
            }
            devices.append(device_info)
            _LOGGER.debug("Found iPIXEL device: %s", device_info)

    try:
        scanner = BleakScanner(detection_callback)
        await scanner.start()
        await asyncio.sleep(timeout)
        await scanner.stop()
        
        _LOGGER.debug("Discovery completed, found %d devices", len(devices))
        return devices
        
    except BleakError as err:
        _LOGGER.error("Discovery failed: %s", err)
        return []