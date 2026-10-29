"""Minimal reference client for ParkStart Gouda (parkstart-gouda.parkpermit.eu).

Stdlib only. Credentials come from PARKSTART_EMAIL / PARKSTART_PASSWORD.

    python3 parkstart_client.py permits|plates|sessions
    python3 parkstart_client.py start <PLATE> <MINUTES>   # costs money
    python3 parkstart_client.py stop <SESSION_ID>
"""
import http.cookiejar
import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://parkstart-gouda.parkpermit.eu"
SESSIONS_FILTER = {
    "skip": 0, "take": 50, "requireTotalCount": "true", "cstId": 0, "vstId": 0, "searchString": "",
    "startIndex": 0, "pageSize": 0, "sortColumn": "", "sortDirection": "", "totalRows": 0,
    "pageCount": -2147483648,
}


class ParkStartError(Exception):
    pass


class ParkStart:
    def __init__(self, email, password, base=BASE):
        self.email, self.password, self.base = email, password, base
        # /etc/ssl/cert.pem: python.org builds on macOS ship without a CA bundle
        cafile = "/etc/ssl/cert.pem" if os.path.exists("/etc/ssl/cert.pem") else None
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=cafile)),
        )
        self._opener.addheaders = [("User-Agent", "Mozilla/5.0")]
        self._model = None

    def _request(self, path, data=None, content_type=None, referer=None, ajax=False):
        req = urllib.request.Request(self.base + path, data=data)
        if content_type:
            req.add_header("Content-Type", content_type)
        if ajax:
            # Without these the server answers 302 -> /Error/Error
            req.add_header("X-Requested-With", "XMLHttpRequest")
            req.add_header("Origin", self.base)
            req.add_header("Referer", self.base + (referer or "/Customer/PlanSession"))
        with self._opener.open(req) as resp:
            return resp.geturl(), resp.read().decode("utf-8")

    def _ajax(self, path, body, referer=None, form=False):
        if form:
            data, ctype = urllib.parse.urlencode(body).encode(), "application/x-www-form-urlencoded; charset=UTF-8"
        else:
            data, ctype = json.dumps(body).encode(), "application/json; charset=utf-8"
        url, text = self._request(path, data, ctype, referer, ajax=True)
        if not url.startswith(self.base + path.rstrip("/")):
            raise ParkStartError(f"redirected to {url} (session expired or missing headers)")
        return json.loads(text)

    def login(self):
        """One attempt only: failed logins can enable a reCAPTCHA."""
        _, html = self._request("/")
        token = re.search(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"', html).group(1)
        form = urllib.parse.urlencode({
            "__RequestVerificationToken": token, "Email": self.email, "Password": self.password,
            "ReturnUrl": "", "ShowCaptcha": "False", "ScreenWidth": "1440", "ScreenHeight": "900",
        }).encode()
        url, page = self._request("/", form, "application/x-www-form-urlencoded")
        if not url.endswith("/Customer/PlanSession"):
            raise ParkStartError(f"login failed (landed on {url})")
        self._model = self._parse_model(page)

    @staticmethod
    def _parse_model(page):
        raw = re.search(r"planSession\.init\('(.*?)'\);", page, re.S).group(1)
        return json.loads(raw.replace('\\"', '"').replace("\\\\", "\\"))

    @property
    def permits(self):
        return self._model["permitList"]

    def license_plates(self, permit_id=None):
        permit_id = permit_id or self.permits[0]["id"]
        return self._ajax("/Customer/PlanSession/GetLicensePlates", {"permitId": permit_id})["licensePlates"]

    def sessions(self):
        """Active and planned sessions. timeStartUtc/timeEndUtc are Europe/Amsterdam local time."""
        return self._ajax("/Customer/SessionsOverview/GetParkingSessions/", SESSIONS_FILTER,
                          referer="/Customer/SessionsOverview/Index/", form=True)["data"]

    def start_session(self, plate, minutes, start=None, permit_id=None):
        permit_id = permit_id or self.permits[0]["id"]
        start = (start or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(second=0, microsecond=0)
        item = dict(self._model["addItem"])
        item.update({
            "permitId": permit_id, "currentPermitId": permit_id, "lp": plate.upper().replace("-", ""),
            "timeStart": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "timeEnd": (start + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "saveMode": True,
        })
        res = self._ajax("/Customer/PlanSession/StartParkingSession", item)
        if not res.get("successful"):
            raise ParkStartError(f"start failed: {res}")
        return res

    def stop_session(self, session_id):
        res = self._ajax("/Customer/PlanSession/StopParkingSession", {"parkingSessionId": int(session_id)},
                         referer="/Customer/SessionsOverview/Index/")
        if not res.get("successful"):
            raise ParkStartError(f"stop failed: {res}")
        return res


def main(argv):
    if not argv:
        sys.exit(__doc__)
    client = ParkStart(os.environ["PARKSTART_EMAIL"], os.environ["PARKSTART_PASSWORD"])
    client.login()
    cmd, args = argv[0], argv[1:]
    result = {
        "permits": lambda: client.permits,
        "plates": lambda: client.license_plates(),
        "sessions": lambda: client.sessions(),
        "start": lambda: client.start_session(args[0], int(args[1])),
        "stop": lambda: client.stop_session(args[0]),
    }[cmd]()
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
