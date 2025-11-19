"""iPIXEL Color Bluetooth API client."""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Any
from zlib import crc32

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError
from PIL import Image, ImageDraw, ImageFont

from homeassistant.exceptions import HomeAssistantError

from .const import WRITE_UUID, NOTIFY_UUID, DEVICE_NAME_PREFIX, CONNECTION_TIMEOUT, RECONNECT_ATTEMPTS, RECONNECT_DELAY

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
        self._device_info: dict[str, Any] | None = None
        
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
    
    async def get_device_info(self) -> dict[str, Any] | None:
        """Query device information and store it."""
        if self._device_info is not None:
            return self._device_info
            
        try:
            # Send device info query command (from go-ipxl device_info.go)
            # Format: [8, 0, 1, 128, hour, minute, second, language]
            import time
            now = time.localtime()
            command = bytes([
                8,              # Command header
                0,              # Reserved 
                1,              # Sub-command
                128,            # 0x80 (corresponds to -128 in signed byte)
                now.tm_hour,    # Current hour
                now.tm_min,     # Current minute  
                now.tm_sec,     # Current second
                0               # Language (0 for default)
            ])
            
            # Set up notification response
            self._device_response = None
            response_received = asyncio.Event()
            
            def response_handler(sender: Any, data: bytearray) -> None:
                self._device_response = bytes(data)
                response_received.set()
            
            # Enable notifications temporarily
            await self._client.start_notify(NOTIFY_UUID, response_handler)
            
            try:
                # Send command
                await self._client.write_gatt_char(WRITE_UUID, command)
                
                # Wait for response (5 second timeout)
                await asyncio.wait_for(response_received.wait(), timeout=5.0)
                
                if self._device_response:
                    self._device_info = self._parse_device_response(self._device_response)
                else:
                    raise Exception("No response received")
                    
            finally:
                await self._client.stop_notify(NOTIFY_UUID)
            
            _LOGGER.info("Device info retrieved: %s", self._device_info)
            return self._device_info
            
        except Exception as err:
            _LOGGER.error("Failed to get device info: %s", err)
            # Return default values
            self._device_info = {
                "width": 64,
                "height": 16,
                "device_type": "Unknown", 
                "mcu_version": "Unknown",
                "wifi_version": "Unknown"
            }
            return self._device_info
    
    def _parse_device_response(self, response: bytes) -> dict[str, Any]:
        """Parse device info response (from go-ipxl parseDeviceInfo)."""
        if len(response) < 5:
            raise Exception(f"Response too short: got {len(response)} bytes, need at least 5")
        
        _LOGGER.debug("Device response: %s", response.hex())
        
        # Device type from byte 4
        device_type_byte = response[4]
        
        # Device type mapping (from go-ipxl consts.go)
        device_type_map = {
            0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8, 9: 9,
            10: 10, 11: 11, 12: 12, 13: 13, 14: 14, 15: 15, 16: 16, 17: 17,
            18: 18, 19: 19, 20: 20, 21: 21, 22: 22, 23: 23, 24: 24, 25: 25,
            26: 26, 27: 27, 28: 28, 29: 29, 30: 30, 31: 31, 32: 32, 33: 33,
            34: 34, 35: 35, 36: 36, 37: 37, 38: 38, 39: 39, 40: 40, 41: 41,
            42: 42, 43: 43, 44: 44, 45: 45, 46: 46, 47: 47, 48: 48, 49: 49,
            50: 50
        }
        
        # LED size mapping (from go-ipxl consts.go) 
        led_size_map = {
            0: [64, 64], 1: [64, 32], 2: [32, 32], 3: [16, 16], 4: [32, 8], 
            5: [64, 16], 6: [96, 8], 7: [128, 8], 8: [192, 8], 9: [320, 8],
            10: [8, 32], 11: [8, 8], 12: [96, 32], 13: [128, 32], 14: [192, 32],
            15: [256, 32], 16: [320, 32], 17: [128, 16], 18: [192, 16], 19: [256, 16],
            20: [320, 16], 21: [160, 32], 22: [384, 32], 23: [512, 32], 24: [384, 16],
            25: [512, 16], 26: [448, 32], 27: [576, 32], 28: [640, 32], 29: [768, 32],
            30: [896, 32], 31: [1024, 32], 32: [448, 16], 33: [576, 16], 34: [640, 16],
            35: [768, 16], 36: [896, 16], 37: [1024, 16], 38: [32, 16], 39: [96, 16],
            40: [160, 16], 41: [224, 16], 42: [288, 16], 43: [352, 16], 44: [416, 16],
            45: [480, 16], 46: [544, 16], 47: [608, 16], 48: [672, 16], 49: [736, 16],
            50: [800, 16]
        }
        
        led_type = device_type_map.get(device_type_byte, 0)
        width, height = led_size_map.get(led_type, [64, 64])
        
        device_info = {
            "width": width,
            "height": height,
            "device_type": f"Type {device_type_byte}",
        }
        
        # Parse version info if response is long enough
        if len(response) >= 8:
            # MCU Version (bytes 4-5)
            mcu_major = response[4]  
            mcu_minor = response[5]
            device_info["mcu_version"] = f"{mcu_major}.{mcu_minor:02d}"
            
            # WiFi Version (bytes 6-7)
            wifi_major = response[6]
            wifi_minor = response[7] 
            device_info["wifi_version"] = f"{wifi_major}.{wifi_minor:02d}"
        else:
            device_info["mcu_version"] = "Unknown"
            device_info["wifi_version"] = "Unknown"
            
        return device_info

    async def display_text(self, text: str) -> bool:
        """Display text as image using PIL."""
        try:
            # Get device dimensions
            device_info = await self.get_device_info()
            width = device_info["width"]
            height = device_info["height"]
            
            # Create image with device dimensions
            img = Image.new('RGB', (width, height), (0, 0, 0))
            draw = ImageDraw.Draw(img)
            
            # Draw white text
            draw.text((2, 2), text, fill=(255, 255, 255))
            
            # Convert to PNG bytes
            png_buffer = io.BytesIO()
            img.save(png_buffer, format='PNG')
            png_data = png_buffer.getvalue()
            
            # Send as PNG (0x0002 command like ipixel-ctrl)
            data_size = len(png_data)
            data_crc = crc32(png_data) & 0xFFFFFFFF
            
            # First enable DIY mode (like go-ipxl examples)
            diy_command = bytes([5, 0, 4, 1, 3])  # Enable DIY mode with clear screen
            await self._send_command(diy_command)
            
            # Build PNG command following ipixel-ctrl format exactly
            payload = bytearray()
            payload.append(0x00)  # Fixed byte
            payload.extend(data_size.to_bytes(4, 'little'))  # Data size
            payload.extend(data_crc.to_bytes(4, 'little'))   # CRC32
            payload.append(0x00)  # Fixed byte  
            payload.append(0x01)  # Buffer number (screen 1)
            payload.extend(png_data)  # PNG data
            
            # Create complete command with length header
            total_length = len(payload) + 4  # +4 for length and command
            command = bytearray()
            command.extend(total_length.to_bytes(2, 'little'))  # Length
            command.extend([0x02, 0x00])  # Command 0x0002 (little-endian)
            command.extend(payload)
            
            success = await self._send_command(bytes(command))
            if success:
                _LOGGER.info("Text image sent successfully: %s (size: %dx%d, PNG: %d bytes)", 
                           text, width, height, data_size)
            else:
                _LOGGER.error("Failed to send text image")
            return success
            
        except Exception as err:
            _LOGGER.error("Error displaying text: %s", err)
            return False

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