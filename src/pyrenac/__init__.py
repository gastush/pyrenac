"""LIbrary providing access to the Renac SEC APIs.

Beware that this library as only been tested against On-Grid Inverters
"""

import asyncio
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
import logging
import threading
from typing import Any, TypeVar

import aiohttp
import requests

__all__ = [
    "run_coroutine_sync",
]

T = TypeVar("T")


def run_coroutine_sync(coroutine: Coroutine[Any, Any, T], timeout: float = 30) -> T:
    def run_in_new_loop():
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        try:
            return new_loop.run_until_complete(coroutine)
        finally:
            new_loop.close()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)

    if threading.current_thread() is threading.main_thread():
        if not loop.is_running():
            return loop.run_until_complete(coroutine)
        else:
            with ThreadPoolExecutor() as pool:
                future = pool.submit(run_in_new_loop)
                return future.result(timeout=timeout)
    else:
        return asyncio.run_coroutine_threadsafe(coroutine, loop).result()


API_ROOT = "https://sec.bg.renacpower.cn:8084/api/"
RENAC_API_ROOT = "https://sec.bg.renacpower.cn:8084/renac/"
BG_API_ROOT = "https://sec.bg.renacpower.cn:8084/bg/"

_LOGGER = logging.getLogger(__name__)

InverterType = Enum("InverterType", ["ONGRID", "HYBRID"])

class Inverter:
    """The base class that represent the Inverter."""

    def __init__(self, api_client) -> None:
        """Initialize the Inverter."""
        self.api_client = api_client
        self._manufacturer = None
        self._model = None
        self._version = None
        self._type = None

    def _get_base_data(self):
        pass

    @property
    def type(self) -> Any:
        """The Inverter type."""
        return self._type


class OnGridInverter(Inverter):
    """An On-Grid ivnerter."""

    def __init__(self, api_client) -> None:
        """Initialize the Inverter."""
        super().__init__(api_client)
        self._type = InverterType.ONGRID


class HybridInverter(Inverter):
    """An hybrid inverter."""

    def __init__(self, api_client) -> None:
        """Initialize the Inverter."""
        super().__init__(api_client)
        self._type = InverterType.HYBRID


@dataclass
class RenacInverterData:
    """Renac inverter data class."""

    name: str
    version: str
    fwversion: str
    registration_time: str
    equipment_type: str
    equipment_serial: str


class PyRenac:
    """The API wrapper."""

    def __init__(self, username, password) -> None:
        """Initialize the librqry."""
        _LOGGER.info("New PyRenac instance %s", username)
        self.username = username
        self.password = password
        self.emailSn = None
        self.equipSn = None
        self.token = None
        self.station_id = None
        self.inverterData = None
        inverterData = run_coroutine_sync(self.async_get_inverter_data())
        self.equipSn = inverterData.equipment_serial
        self.inverterData = inverterData

    def getSerial(self):
        """Get the serial number of the inverter."""
        return self.equipSn

    def getUniqueId(self, field):
        """Get a unique id for q given field name."""
        return "renac_" + field + "_" + self.equipSn

    def _login_request(self):
        return {"loginName": self.username, "password": self.password}

    def _fetch_request(self):
        return {"sn": self.equipSn, "email": self.emailSn}

    async def async_login(self) -> None:
        """Login to the Renac SEC API backend."""
        _LOGGER.info("Requesting authorization")
        req_json = self._login_request()
        async with (
            aiohttp.ClientSession() as session,
            session.post(API_ROOT + "login/", json=req_json) as resp,
        ):
            _LOGGER.debug(resp.status)
            loginResponse = await resp.json(content_type=None)
            _LOGGER.debug("Got %s", loginResponse.get("email"))
            self.emailSn = loginResponse.get("email")
            self.token = loginResponse.get("Token")

    def login(self) -> None:
        """Login to the Renac SEC API backend."""
        _LOGGER.info("Requesting authorization")
        req_json = self._login_request()
        resp = requests.post(API_ROOT + "login", json=req_json, timeout=60)
        if resp.status_code == 200:
            loginResponse = resp.json()
            _LOGGER.debug("Got %s", loginResponse.get("email"))
            self.emailSn = loginResponse.get("email")
            self.token = loginResponse.get("Token")

    def fetch_field_value(self, data, field):
        """Fetch the given field from the data."""
        _LOGGER.debug("Fetch field value %s", field)
        if data is not None:
            return data.get(field)
        return None

    async def async_fetch(self, field):
        """Fetch the data identified by the field."""
        data = await self.async_fetch_all()
        if data is not None:
            return data.get(field)
        return None

    def fetch(self, field):
        """Fetch the data identified by the field."""
        data = self.fetch_all()
        if data is not None:
            return data.get(field)
        return None

    async def async_ensure_login(self):
        """Ensure that we have a valid Token to be used."""
        if self.token is None:
            _LOGGER.info("Token is null, new fresh login sequence required")
            await self.async_login()

    def ensure_login(self):
        """Ensure that we have a valid Token to be used."""
        if self.token is None:
            _LOGGER.info("Token is null, new fresh login sequence required")
            self.login()

    async def async_fetch_all(self):
        """Fetch all the data.

        A login will be done if needed to retrieve the right token.
        """
        _LOGGER.debug("Fetching all data")
        data = None
        await self.async_ensure_login()
        req_json = {"sn": self.equipSn, "email": self.emailSn}
        headers = {"Token": self.token}
        timeout = aiohttp.ClientTimeout(total=30)
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                API_ROOT + "equipDetail/",
                json=req_json,
                headers=headers,
                timeout=timeout,
            ) as resp,
        ):
            if resp.status == 200:
                response = await resp.json(content_type=None)
                if "results" in response:
                    data = response.get("results")
                else:
                    _LOGGER.info("Null results. assuming a new Token is required")
                    self.token = None
            else:
                raise ("Failed to read sensor " + str(resp.status))

        return data

    def fetch_all(self):
        """Fetch all the data.

        A login will be done if needed to retrieve the right token.
        """
        _LOGGER.debug("Fetching all data")
        data = None
        self.ensure_login()
        req_json = {"sn": self.equipSn, "email": self.emailSn}
        headers = {"Token": self.token}
        resp = requests.post(
            API_ROOT + "equipDetail/", headers=headers, json=req_json, timeout=60
        )
        if resp.status == 200:
            response = resp.json()
            if "results" in response:
                data = response.get("results")
            else:
                _LOGGER.info("Null results. assuming a new Token is required")
                self.token = None
        else:
            raise ("Failed to read sensor " + str(resp.status))

        return data

    def getType(self, data) -> InverterType:
        """Get the Inverter Type from the availqble fields."""
        inverterType = None
        try:
            value = self.fetch_field_value(
                data, "BATTERY_CAPACITY"
            )  # HYBRID inverter have a battery.
            if value is None:
                inverterType = InverterType.ONGRID
            else:
                inverterType = InverterType.HYBRID
        except KeyError:
            inverterType = InverterType.ONGRID  # Assume this is an On-Grid inverter.
        return inverterType

    def _station_list_request(self):
        return {
            "export_type": 0,
            "installer_name": "",
            "offset": 0,
            "rows": 10,
            "station_name": "",
            "station_type": None,
            "status": None,
            "user_id": self.emailSn,
            "user_name": "",
        }

    async def async_get_station_id(self):
        """Get the station id."""
        if self.station_id is None:
            await self.async_ensure_login()
            req_json = self._station_list_request()
            headers = {"Token": self.token}
            timeout = aiohttp.ClientTimeout(total=30)
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    API_ROOT + "station/list",
                    json=req_json,
                    headers=headers,
                    timeout=timeout,
                ) as resp,
            ):
                if resp.status == 200:
                    response = await resp.json(content_type=None)
                    if "data" in response:
                        _LOGGER.warning(response)
                        self.station_id = response["data"]["list"][0]["station_id"]
                    else:
                        _LOGGER.info("Null results. assuming a new Token is required")
                        self.token = None
                else:
                    raise ("Failed to read sensor " + str(resp.status))
            _LOGGER.info("Got station_id %s", self.station_id)
        return self.station_id

    def get_station_id(self):
        """Get the station id."""
        if self.station_id is None:
            self.ensure_login()
            req_json = self._station_list_request()
            headers = {"Token": self.token}
            resp = requests.post(
                API_ROOT + "station/list", headers=headers, json=req_json, timeout=60
            )
            if resp.status == 200:
                response = resp.json()
                if "data" in response:
                    self.station_id = response["data"]["list"][0]["station_id"]
                else:
                    _LOGGER.info("Null results. assuming a new Token is required")
                    self.token = None
            else:
                raise ("Failed to read sensor " + str(resp.status))
            _LOGGER.info("Got station_id %s", self.station_id)
        return self.station_id

    async def async_get_historical_data(self, date):
        """Get Historical production data for the given date time range."""
        data = None
        await self.ensure_login()
        station_id = await self.get_station_id()
        req_json = {"station_id": station_id, "time": str(date), "time_type": 1}
        headers = {"Token": self.token}
        timeout = aiohttp.ClientTimeout(total=30)
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                RENAC_API_ROOT + "station/energy",
                json=req_json,
                headers=headers,
                timeout=timeout,
            ) as resp,
        ):
            if resp.status == 200:
                response = await resp.json(content_type=None)
                if "data" in response:
                    data = response["data"]
                else:
                    _LOGGER.info("Null results. assuming a new Token is required")
                    self.token = None
            else:
                raise ("Failed to read sensor " + str(resp.status))
        return data

    def get_inverter_data(self) -> RenacInverterData:
        """Get details about the inverter itslef."""
        inverterData = None
        self.ensure_login()
        station_id = self.get_station_id()
        req_json = {
            "station_id": station_id,
            "user_id": self.emailSn,
            "status": 0,
            "offset": 0,
            "rows": 1,
        }
        headers = {"Token": self.token}
        resp = requests.post(
            API_ROOT + "station/list", headers=headers, json=req_json, timeout=60
        )
        if resp.status == 200:
            response = resp.json(content_type=None)
            if "data" in response:
                data = response["data"]["list"][0]
                inverterData = RenacInverterData(
                    version=data[0].get("VERSION"),
                    fwversion=data[0].get("FIRMWARE_VER"),
                    name=data[0].get("STATION_NAME"),
                    equipment_type=data[0].get("EQU_TYPE"),
                    registration_time=data[0].get("REG_TIME"),
                    equipment_serial=data[0].get("INV_SN"),
                )
            else:
                _LOGGER.info("Null results. assuming a new Token is required")
                self.token = None
        else:
            raise ("Failed to read sensor " + str(resp.status))
        return inverterData

    async def async_get_inverter_data(self) -> RenacInverterData:
        """Get details about the inverter itslef."""
        if self.inverterData is None:
            await self.async_ensure_login()
            station_id = await self.async_get_station_id()
            req_json = {
                "station_id": station_id,
                "user_id": self.emailSn,
                "status": 0,
                "offset": 0,
                "rows": 1,
            }
            headers = {"Token": self.token}
            timeout = aiohttp.ClientTimeout(total=30)
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    BG_API_ROOT + "equList",
                    json=req_json,
                    headers=headers,
                    timeout=timeout,
                ) as resp,
            ):
                if resp.status == 200:
                    response = await resp.json(content_type=None)
                    if "data" in response:
                        _LOGGER.warning(response)
                        data = response["data"]["list"]
                        if len(data) > 0:
                            self.inverterData = RenacInverterData(
                                version=data[0].get("VERSION"),
                                fwversion=data[0].get("FIRMWARE_VER"),
                                name=data[0].get("STATION_NAME"),
                                equipment_type=data[0].get("EQU_TYPE"),
                                registration_time=data[0].get("REG_TIME"),
                                equipment_serial=data[0].get("INV_SN"),
                            )
                    else:
                        _LOGGER.info("Null results. assuming a new Token is required")
                        self.token = None
                else:
                    raise ("Failed to read sensor " + str(resp.status))
        return self.inverterData


class InverterFactory:
    """The inverter factory."""

    def getInverter(self, api_client: PyRenac) -> Inverter:
        """Get the inverter based on his type."""
        inverterType = api_client.getType()
        if inverterType is inverterType.ONGRID:
            return OnGridInverter(api_client)
        if inverterType is InverterType.HYBRID:
            return HybridInverter(api_client)
        raise ValueError("Unsupported Inverter type")
