"""DEV/DEMO ONLY: reset the local database and play the AutoQ demo story through the real API.

Every fact is created through the same HTTP endpoints the UI uses (in-process TestClient), so every row
has its audit event, outbox row, signature and evidence exactly as production code would write them.
All images are SYNTHETIC_TEST renders and the inspection profiles are calibrated on a SYNTHETIC
evaluation; the UI badges both. Nothing here is evidence about a physical part.

    make seed-demo            # == cd backend && uv run python scripts/seed_demo.py --reset

The story (one bracket part, two lines, three heats):
  H-7781 → L-2611-A (Line 2, 40 units): sample PASS, 24 extra individual PASS, 1 REJECT, 1 HOLD (blurry)
  H-7790 → L-2611-B (Line 2, 12 units): sample PASS
  H-7795 → L-2612-C (Line 2, 10 units): every unit inspected with rising tool wear → SPC drift warning
  H-7795 → L-2613-D (Line 3,  8 units): sample PASS, then an unplanned 4M MACHINE change → containment ACTIVE
  SO-1002 Pune     : 4 × A, delivered
  SO-1003 Chennai  : 2 × A + 2 × B (mixed lot), in transit
  SO-1004 Gurugram : 4 × A, loaded and manifest closed, not departed
  SO-1001 Gurugram : the live dock demo — open shipment WB-25-K-4471 on Dock 3 with
                     PKG A (4 × A, fresh INTACT recheck → scan ACCEPTED),
                     PKG B (4 × B, no recheck yet → SEAL_RECHECK_REQUIRED until the dispatcher rechecks),
                     PKG C (2 × A, torn seal → SEAL_REVIEW, siren)
Recall of H-7781 is left for the presenter to run live from Trace."""
import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.core.config import disposable_database_problem, settings  # noqa: E402
from app.features.console_demo.synthetic import render, render_seal  # noqa: E402

API = "/api/v1"
PDF = b"%PDF-1.4\n% SYNTHETIC demo mill test certificate\n"
SYN = "SYNTHETIC_TEST"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "db", "postgres"}


class Demo:
    def __init__(self, client, log=print):
        self.c, self.log, self._tok = client, log, {}

    def h(self, user: str) -> dict:
        if user not in self._tok:
            r = self.c.post(f"{API}/auth/login", json={"username": user, "password": "autoq-dev"})
            r.raise_for_status()
            self._tok[user] = {"Authorization": f"Bearer {r.json()['access_token']}"}
        return self._tok[user]

    def call(self, method: str, path: str, user: str, expect=(200, 201), **kw):
        r = self.c.request(method, API + path, headers=self.h(user), **kw)
        if r.status_code not in expect:
            raise RuntimeError(f"{method} {path} as {user} → {r.status_code}: {r.text[:400]}")
        return r.json()

    post = lambda self, p, u, **kw: self.call("POST", p, u, **kw)  # noqa: E731

    # ── building blocks ──
    def profile(self, part_id: str, station: str) -> dict:
        p = self.post("/inspection-profiles", "qe.ananya", json={"part_id": part_id, "station_id": station})
        g = self.post(f"/inspection-profiles/{p['id']}/golden", "qe.ananya", files={"image": ("golden.jpg", render("golden"), "image/jpeg")})
        ev = {"method_version": g["method_version"], "samples": 40, "false_accepts": 0, "false_rejects": 1, "unverifiable": 2, "evidence_source": SYN}
        return self.post(f"/inspection-profiles/{g['id']}/calibrate", "qm.farhan",
                         data={"hold": 0.45, "reject": 0.70, "reason": "Demo calibration on a SYNTHETIC held-out set (not physical evidence)"},
                         files={"evaluation": ("eval.json", json.dumps(ev).encode(), "application/json")})

    def heat(self, code: str, supplier_id: str, grade="SG 500/7") -> dict:
        return self.post("/heats", "qe.ananya", data={"heat_code": code, "supplier_id": supplier_id, "material_grade": grade},
                         files={"certificate": (f"MTC-{code}.pdf", PDF + code.encode(), "application/pdf")})

    def lot(self, code: str, part: str, line: str, heat: str, n: int) -> tuple[dict, list[dict], list[str]]:
        lot = self.post("/lots", "op.ravi", json={"lot_code": code, "part_id": part, "line_id": line, "heat_ids": [heat], "planned_qty": n})
        units = self.post(f"/lots/{lot['id']}/units", "op.ravi", json={"count": n})["items"]
        sample = self.post(f"/lots/{lot['id']}/freeze-and-sample", "qe.ananya")["sample_unit_ids"]
        return lot, units, sample

    def inspect(self, unit_id: str, kind="good", seed=1, wear=0.0, station="st_l2_vis") -> dict:
        return self.post("/inspections", "op.ravi", data={"unit_id": unit_id, "evidence_source": SYN, "station_id": station},
                         files={"image": (f"{unit_id}.jpg", render(kind, seed, wear), "image/jpeg")})

    def package(self, order_id: str, unit_ids: list[str], seed: int, recheck: str | None = "intact") -> dict:
        p = self.post("/packages", "op.ravi", json={"order_id": order_id})
        for u in unit_ids:
            self.post(f"/packages/{p['id']}/items", "op.ravi", json={"unit_id": u})
        s = self.post(f"/packages/{p['id']}/seal", "op.ravi", data={"evidence_source": SYN},
                      files={"image": ("seal.jpg", render_seal("enroll", seed), "image/jpeg")})
        if recheck:
            self.post(f"/packages/{p['id']}/seal-checks", "disp.kiran", data={"evidence_source": SYN},
                      files={"image": ("recheck.jpg", render_seal(recheck, seed + 1), "image/jpeg")})
        return s

    def ship(self, order_id: str, pkgs: list[dict], vehicle: str, dock: str, *, close=True, depart=False, deliver=False, at="") -> dict:
        s = self.post("/shipments", "disp.kiran", json={"order_id": order_id, "vehicle_no": vehicle, "dock": dock})
        for i, p in enumerate(pkgs):
            d = self.post(f"/shipments/{s['id']}/scans", "disp.kiran", json={"token": p["token"], "client_event_id": f"seed-{s['id']}-{i}"})
            if d["decision"] != "ACCEPTED":
                raise RuntimeError(f"seed scan blocked: {d['reasons']}")
        if close:
            self.post(f"/shipments/{s['id']}/close", "disp.kiran")
        if depart:
            self.post(f"/shipments/{s['id']}/events", "disp.kiran", json={"type": "DEPARTED", "location": "Plant gate 2"})
        if deliver:
            self.post(f"/shipments/{s['id']}/events", "disp.kiran", json={"type": "DELIVERED", "location": at})
        return s

    # ── the story ──
    def run(self, scanner_dock: bool = False) -> dict:
        log = self.log
        sup1 = self.post("/suppliers", "admin", json={"code": "SUP-014", "name": "Shree Forgings", "synthetic": True})
        sup2 = self.post("/suppliers", "admin", json={"code": "SUP-022", "name": "Vardhman Castings", "synthetic": True})
        part = self.post("/parts", "admin", json={"part_number": "PN-44120", "name": "Suspension bracket", "drawing_rev": "C",
                                                  "is_a_part": False, "default_sample_size": 10})
        l2 = self.post("/lines", "admin", json={"name": "Line 2", "station_id": "st_l2_vis"})
        l3 = self.post("/lines", "admin", json={"name": "Line 3", "station_id": "st_l3_vis"})
        cus = {city: self.post("/customers", "admin", json={"name": name, "city": city})
               for name, city in [("Demo Assembly Plant", "Gurugram"), ("Northline Motors", "Pune"), ("Coastal Auto", "Chennai")]}
        for st in ("st_l2_vis", "st_l3_vis"):
            self.profile(part["id"], st)
        log("master data + SYNTHETIC calibrated profiles")

        h1, h2, h3 = self.heat("H-7781", sup1["id"]), self.heat("H-7790", sup2["id"]), self.heat("H-7795", sup1["id"])
        _, A, sA = self.lot("L-2611-A", part["id"], l2["id"], h1["id"], 40)
        for i, u in enumerate(sA):
            self.inspect(u, seed=600 + i)
        rest = [u["id"] for u in A if u["id"] not in sA]
        for i, u in enumerate(rest[:24]):
            self.inspect(u, seed=700 + i)
        rej = self.inspect(rest[24], "defect", seed=7)
        hold = self.inspect(rest[25], "blurry", seed=8)
        log(f"L-2611-A: sample PASS, 24 individual PASS, {rej['verdict']} ({rest[24]}), {hold['verdict']} ({rest[25]})")

        _, B, sB = self.lot("L-2611-B", part["id"], l2["id"], h2["id"], 12)
        for i, u in enumerate(sB):
            self.inspect(u, seed=900 + i)
        _, C, _ = self.lot("L-2612-C", part["id"], l2["id"], h3["id"], 10)
        for i, u in enumerate(C):                       # gradual tool wear on Line 2 → SPC drift
            self.inspect(u["id"], seed=1000 + i, wear=0.30 + 0.035 * i)
        _, D, sD = self.lot("L-2613-D", part["id"], l3["id"], h3["id"], 8)
        for i, u in enumerate(sD):
            self.inspect(u, seed=1100 + i, station="st_l3_vis")
        log("L-2611-B released, L-2612-C wear trend, L-2613-D released")

        pa = [u["id"] for u in A if u["id"] not in (rest[24], rest[25])]
        pb = [u["id"] for u in B]
        o = {no: self.post("/orders", "disp.kiran", json={"order_no": no, "customer_id": cus[city]["id"], "part_id": part["id"], "qty_units": q,
                                                          "destination": dest})
             for no, city, q, dest in [("SO-1001", "Gurugram", 8, "Gurugram – Gate 4"), ("SO-1002", "Pune", 4, "Pune – Chakan plant"),
                                       ("SO-1003", "Chennai", 4, "Chennai – Oragadam"), ("SO-1004", "Gurugram", 4, "Gurugram – Gate 4")]}
        p2 = self.package(o["SO-1002"]["id"], pa[0:4], seed=30)
        p3 = self.package(o["SO-1003"]["id"], pa[4:6] + pb[0:2], seed=40)
        p4 = self.package(o["SO-1004"]["id"], pa[6:10], seed=50)
        self.ship(o["SO-1002"]["id"], [p2], "MH-12-QT-2210", "Dock 1", depart=True, deliver=True, at="Pune – Chakan plant")
        self.ship(o["SO-1003"]["id"], [p3], "TN-09-BX-7731", "Dock 2", depart=True)
        self.ship(o["SO-1004"]["id"], [p4], "HR-55-AK-9004", "Dock 1")
        log("SO-1002 delivered, SO-1003 in transit (mixed A+B), SO-1004 loaded not departed")

        ready = self.package(o["SO-1001"]["id"], pa[10:14], seed=60)
        needs_recheck = self.package(o["SO-1001"]["id"], pb[2:6], seed=70, recheck=None)
        torn = self.package(o["SO-1001"]["id"], pa[14:16], seed=80, recheck="torn")
        dock = self.post("/shipments", "disp.kiran", json={"order_id": o["SO-1001"]["id"], "vehicle_no": "WB-25-K-4471", "dock": "Dock 3"})
        log(f"SO-1001 dock demo: shipment {dock['code']} OPEN · {ready['label']} ready · {needs_recheck['label']} needs recheck · {torn['label']} torn seal")

        fm = self.post("/four-m-events", "qe.ananya", json={"category": "MACHINE", "planned": False, "line_id": l3["id"],
                                                            "description": "Boring insert replaced on OP-20", "reason": "Insert chipped mid-shift"})
        log(f"4M {fm.get('event', fm).get('code', '')} on Line 3 → containment ACTIVE")
        out = {"dock_shipment": dock["code"], "ready_package": ready["label"], "recall_heat": "H-7781"}
        if scanner_dock:   # e2e only: a second open shipment the phone-scanner spec owns, so it never disturbs the dock story
            so5 = self.post("/orders", "disp.kiran", json={"order_no": "SO-1005", "customer_id": cus["Gurugram"]["id"], "part_id": part["id"],
                                                           "qty_units": 4, "destination": "Gurugram – Gate 4"})
            s1 = self.package(so5["id"], pb[6:8], seed=90)
            s2 = self.package(so5["id"], pb[8:10], seed=95)
            sd = self.post("/shipments", "disp.kiran", json={"order_id": so5["id"], "vehicle_no": "HR-26-SC-0505", "dock": "Dock 5"})
            log(f"SO-1005 scanner dock: shipment {sd['code']} OPEN · {s1['label']} + {s2['label']} ready")
            out |= {"scanner_shipment": sd["code"], "scanner_packages": [s1["label"], s2["label"]]}
        return out


def _guard_local() -> None:
    host = urlparse(settings().database_url.replace("+psycopg", "")).hostname or ""
    if host not in LOCAL_HOSTS:
        sys.exit(f"refusing to reset non-local database host {host!r}")
    # Under the Playwright suite only a throwaway `*_e2e` database may be wiped, whatever `.env` says.
    if os.environ.get("AUTOQ_E2E") == "1" and (problem := disposable_database_problem(settings().database_url)):
        sys.exit(f"refusing to reset during an e2e run: {problem}")


def reset_database() -> None:
    from app.db.models import Base
    from app.db.session import session_factory
    from scripts.seed_dev_users import seed
    _guard_local()
    with session_factory()() as db:
        db.execute(text("TRUNCATE " + ", ".join(t.name for t in Base.metadata.sorted_tables) + " RESTART IDENTITY CASCADE"))
        for seq in ("package_label_seq", "shipment_code_seq"):     # free-standing sequences: not owned by a table
            db.execute(text(f"ALTER SEQUENCE IF EXISTS {seq} RESTART WITH 1"))
        seed(db)
        db.commit()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--reset", action="store_true", help="TRUNCATE every table first (local databases only)")
    ap.add_argument("--scanner-dock", action="store_true", help="also stage SO-1005 / a second open shipment for the e2e phone-scanner spec")
    a = ap.parse_args()
    from fastapi.testclient import TestClient

    from app.main import create_app
    if a.reset:
        reset_database()
        print("database reset; dev users + demo-1 policy seeded")
    with TestClient(create_app()) as client:
        out = Demo(client, lambda s: print(" ·", s)).run(scanner_dock=a.scanner_dock)
    print(f"demo ready — dock shipment {out['dock_shipment']}, scan {out['ready_package']} first; recall heat {out['recall_heat']} from Trace")


if __name__ == "__main__":
    main()
