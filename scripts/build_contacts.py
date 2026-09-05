#!/usr/bin/env python3
"""경희대 연락처 정적 데이터 빌더.

입력
  data/contacts/raw/khu_phone_directory_seoul.txt   공식 교내 전화번호 안내(서울) 텍스트 스냅샷
  data/contacts/raw/khu_phone_directory_global.txt  공식 교내 전화번호 안내(국제) 텍스트 스냅샷
  data/contacts/raw/observed_at.txt                 스냅샷 관찰 시각(UTC)
  data/contacts/enrichment.json                     기관 페이지에서 수집한 위치·이메일·업무시간·보정(수기)

출력
  data/contacts/phone_directory.json  전화번호부 구조화 결과(기관 → 업무 → 원문 번호)
  data/contacts/contacts.json         KHU_API_CONTRACT_DRAFT.md 5절 Contact 모델에 맞춘 연락처 목록

원칙: 원문에 없는 값은 만들지 않는다(업무시간·위치는 근거 페이지가 있을 때만 채움).
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "contacts" / "raw"
OUT = ROOT / "data" / "contacts"

DIRECTORY_URL = {
    "seoul": "https://www.khu.ac.kr/kor/user/contents/view.do?menuNo=200091",
    "global": "https://www.khu.ac.kr/kor/user/contents/view.do?menuNo=200092",
}
DIRECTORY_NAME = {"seoul": "경희대학교 교내 전화번호 안내(서울캠퍼스)", "global": "경희대학교 교내 전화번호 안내(국제캠퍼스)"}
CAMPUS = {"seoul": {"id": "campus-seoul", "name": "서울캠퍼스"}, "global": {"id": "campus-global", "name": "국제캠퍼스"}}
AREA = {"seoul": "02-961", "global": "031-201"}  # 외부 발신: 02) 961-내선 / 031) 201-내선
PREFIX = {"seoul": "02", "global": "031"}

SECTIONS = ["대학본부", "대학", "대학원", "부속기관", "부설연구소(원)", "학생회", "편의시설", "관리실", "기타"]
SECTION_TYPE = {
    "대학본부": "office", "대학": "college", "대학원": "college", "부속기관": "institute",
    "부설연구소(원)": "institute", "학생회": "council", "편의시설": "office", "관리실": "office", "기타": "office",
}
TYPE_LABEL = {
    "university": "대학", "campus": "캠퍼스", "college": "단과대학", "department": "학과",
    "office": "행정부서", "council": "학생자치", "institute": "부속·연구기관",
}
VER = {
    "verified": {"code": "verified", "label": "원문 확인됨", "message": "공식 원문의 안내와 일치함을 확인한 시각입니다. 전화 연결이 보장된다는 뜻은 아닙니다."},
    "stale": {"code": "stale", "label": "확인 오래됨", "message": "마지막 확인 후 오래되었습니다. 원문에서 다시 확인해 주세요."},
    "conflict": {"code": "conflict", "label": "내용 충돌 검토 중", "message": "서로 다른 공식 원문의 값이 달라 검토 중입니다. 표시된 값은 각 원문 표기 그대로입니다."},
}

NUM = re.compile(r"^(\d{2,4}-)?\d{3,4}(~\d{1,4})?(\s*[,/]\s*(\d{2,4}-)?\d{3,4}(~\d{1,4})?)*$")


def is_num(line: str) -> bool:
    return bool(NUM.match(line.replace(" ", "")))


def is_label(line: str) -> bool:
    return bool(line) and not is_num(line) and line != "전화번호부"


# --------------------------------------------------------------------------- parse
def parse_directory(path: Path, campus: str):
    lines = [l for l in path.read_text(encoding="utf-8").split("\n") if l]
    start = next(k for k, l in enumerate(lines) if l == "대학본부")
    L = lines[start:]
    orgs, unmatched = [], []
    section = None
    org = None
    group = None
    fresh = False

    def new_org(name: str):
        nonlocal org, group, fresh
        org = {"campus": campus, "section": section, "name": name, "entries": []}
        orgs.append(org)
        group = None
        fresh = True

    k = 0
    while k < len(L):
        l = L[k]
        n1 = L[k + 1] if k + 1 < len(L) else ""
        n2 = L[k + 2] if k + 2 < len(L) else ""
        n3 = L[k + 3] if k + 3 < len(L) else ""
        if l in ("학교법인 경희학원", "교내전화번호"):
            break
        # 섹션 제목(서울: 두 번 반복, 국제: 다음 줄이 기관명)
        if l in SECTIONS and (n1 == l or n1 == "전화번호부" or (is_label(n1) and (n2 == n1 or n2 == "전화번호부"))):
            section = l
            k += 2 if n1 == l else 1
            new_org(l)  # 섹션 바로 아래 항목을 담는 암묵 기관
            if n1 == "전화번호부":
                k += 1
            continue
        # 기관 제목: X,X(서울) 또는 X,전화번호부(국제)
        if is_label(l) and (n1 == l or n1 == "전화번호부"):
            if fresh and n1 == l and is_num(n2) and org and not org["entries"]:
                # 'LINC+사업단 | 서울사무국 | 서울사무국 | 번호' → 새 기관이 아니라 업무 라벨
                org["entries"].append({"group": None, "label": l, "raw": n2, "kind": "phone"})
                fresh = False
                k += 3
                continue
            new_org(l)
            k += 2
            continue
        # 반복 없는 기관 제목: 라벨,라벨,라벨,번호 ('미래혁신원 미래혁신단 | 미래혁신원 | 단장실 | 3050')
        if (is_label(l) and is_label(n1) and is_label(n2) and is_num(n3)
                and not any(x.startswith("- ") for x in (l, n1, n2))):
            new_org(l)
            k += 1
            continue
        if is_num(l):
            unmatched.append((k, l, "number without label"))
            k += 1
            continue
        if is_num(n1):
            if org is None:
                unmatched.append((k, l, "entry without org"))
                k += 2
                continue
            if l.startswith("- "):
                sub, g = l[2:].strip(), group
            else:
                sub, g = l, None
            org["entries"].append({"group": g, "label": sub, "raw": n1, "kind": "fax" if sub.lower() == "fax" else "phone"})
            fresh = False
            k += 2
            continue
        if not l.startswith("- "):
            group = l
        else:
            unmatched.append((k, l, "dash label without number"))
        k += 1
    return orgs, unmatched


# --------------------------------------------------------------------------- numbers
FULL = re.compile(r"^\d{2,4}-\d{3,4}-\d{4}$")
SHORT = re.compile(r"^(?:(\d{3,4})-)?(\d{3,4})(?:~(\d{1,4}))?$")
SPECIAL = re.compile(r"^(15|16|18)\d{2}-\d{4}$")


def expand_numbers(raw: str, campus: str) -> list[dict]:
    """'0053~4, 0057' → [{display, value, values, extension}, ...] 원문 표기는 보존."""
    out = []
    inherited_pre = None  # '958-4616, 4630' → 4630도 958 국번을 이어받음
    for part in [p.strip() for p in re.split(r"[,/]", raw) if p.strip()]:
        if FULL.match(part) or SPECIAL.match(part):
            out.append({"display": part, "value": part, "values": [part], "extension": None})
            inherited_pre = None
            continue
        m = SHORT.match(part)
        if not m:
            out.append({"display": part, "value": None, "values": [], "extension": None})
            continue
        pre, base, end = m.groups()
        if pre:
            inherited_pre = pre
        elif inherited_pre:
            pre = inherited_pre
        values = [base]
        if end:
            end_full = base[: len(base) - len(end)] + end if len(end) < len(base) else end
            if end_full.isdigit() and int(end_full) > int(base) and int(end_full) - int(base) <= 20:
                width = len(base)
                values = [str(n).zfill(width) for n in range(int(base), int(end_full) + 1)]
        if pre:
            full = [f"{PREFIX[campus]}-{pre}-{v}" for v in values]
            ext = None
        else:
            full = [f"{AREA[campus]}-{v}" for v in values]
            ext = base
        out.append({"display": part, "value": full[0], "values": full, "extension": ext})
    return out


def phone_channels(cid: str, raw: str, campus: str, kind: str, start: int = 0) -> list[dict]:
    chans = []
    for i, n in enumerate(expand_numbers(raw, campus), start):
        chans.append({
            "id": f"{cid}-{'f' if kind == 'fax' else 'p'}{i}",
            "kind": {"code": kind, "label": "팩스" if kind == "fax" else "전화"},
            "display_value": n["display"],
            "value": n["value"],
            "values": n["values"],
            "action_url": (f"tel:{n['value'].replace('-', '')}" if kind == "phone" and n["value"] else None),
            "extension": n["extension"],
        })
    return chans


def hid(*parts: str, n: int = 10) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:n]


# --------------------------------------------------------------------------- contacts
DEPT_RE = re.compile(r"(학과|학부|전공|공학|.학)$")
NON_DEPT = {"학장실", "행정실", "FAX", "Fax", "교수휴게실", "명예교수실", "대학원", "대학장실", "대학장행정실", "학부장실", "학과장실"}
COUNCIL_SUFFIX_SKIP = ("학생회", "연합회", "응원단", "편집실", "동아리", "총학생회")


def build_contacts(parsed: dict, observed_at: str) -> list[dict]:
    contacts: list[dict] = []
    for campus, orgs in parsed.items():
        camp = CAMPUS[campus]
        evidence_base = {"url": DIRECTORY_URL[campus], "source_name": DIRECTORY_NAME[campus], "observed_at": observed_at}
        for org in orgs:
            section = org["section"]
            implicit = org["name"] == section
            otype = SECTION_TYPE.get(section, "office")
            last: dict | None = None
            for e in org["entries"]:
                label, group, raw, kind = e["label"], e["group"], e["raw"], e["kind"]
                if kind == "fax":
                    if last is not None:
                        cid = last["id"]
                        n = sum(1 for c in last["channels"] if c["kind"]["code"] == "fax")
                        chans = phone_channels(cid, raw, campus, "fax", n)
                        last["channels"].extend(chans)
                        last["evidence"].extend({"field": f"channels.{c['id']}", **evidence_base} for c in chans)
                    continue
                # 기관·경로 결정
                path = ["경희대학교", camp["name"]]
                org_name, service, ctype = org["name"], label, otype
                if implicit:
                    # 섹션 바로 아래 나열된 항목은 각각 독립 기관(부설연구소, 서울 학생회, 기타 등)
                    org_name = label
                    if section == "학생회" and not any(s in label for s in COUNCIL_SUFFIX_SKIP):
                        org_name = f"{label} 학생회"
                    service = "대표 문의"
                elif section in ("대학", "대학원") and (group == "학과사무실" or (label not in NON_DEPT and DEPT_RE.search(label))):
                    path.append(org["name"])
                    org_name, service, ctype = label, "학과 사무실", "department"
                elif section == "학생회" and org["name"] == "대학 학생회":
                    org_name, service = f"{label} 학생회", "대표 문의"
                elif section == "학생회" and group:
                    org_name, service = (label if label != group else group), "대표 문의"
                    if label == group:
                        service = "대표 문의"
                elif group:
                    service = f"{group} · {label}" if label != group else group
                path.append(org_name)
                cid = "kc-" + hid(campus, section, org["name"], group or "", label, raw)
                chans = phone_channels(cid, raw, campus, "phone")
                contact = {
                    "id": cid,
                    "kind": "contact",
                    "organization": {
                        "id": "org-" + hid(campus, *path[2:], n=8),
                        "name": org_name,
                        "path": path,
                        "type": {"code": ctype, "label": TYPE_LABEL[ctype]},
                    },
                    "campuses": [camp],
                    "service_name": service,
                    "channels": chans,
                    "location": None,
                    "office_hours": None,
                    "official_url": None,
                    "verification": {**VER["verified"], "verified_at": observed_at},
                    "evidence": [{"field": f"channels.{c['id']}", **evidence_base} for c in chans],
                    "source": {"directory_section": section, "directory_org": org["name"], "group": group, "label": label, "raw": raw},
                }
                contacts.append(contact)
                last = contact
    return contacts


# --------------------------------------------------------------------------- enrichment
def apply_enrichment(contacts: list[dict], enrich: dict, observed_at: str) -> tuple[int, int]:
    applied = 0
    index = {}
    for c in contacts:
        s = c["source"]
        index.setdefault((c["campuses"][0]["id"].split("-")[1], s["directory_org"], s["label"]), []).append(c)
        index.setdefault((c["campuses"][0]["id"].split("-")[1], s["directory_org"], f"group:{s['group']}"), []).append(c)
    for p in enrich.get("patches", []):
        key = (p["campus"], p["org"], p.get("label") or f"group:{p.get('group')}")
        targets = index.get(key, [])
        if not targets:
            print(f"  [warn] patch target not found: {key}", file=sys.stderr)
            continue
        ev = [{"url": e["url"], "source_name": e["source_name"], "observed_at": observed_at} for e in p.get("evidence", [])]
        for c in targets:
            applied += 1
            if p.get("service_name"):
                c["service_name"] = p["service_name"]
            for f in ("location", "office_hours", "official_url"):
                if p.get(f) is not None:
                    c[f] = p[f]
                    c["evidence"].extend({"field": f, **e} for e in ev)
            n_e = sum(1 for ch in c["channels"] if ch["kind"]["code"] == "email")
            for i, mail in enumerate(p.get("emails", []), n_e):
                ch = {"id": f"{c['id']}-e{i}", "kind": {"code": "email", "label": "이메일"}, "display_value": mail,
                      "value": mail if re.match(r"^[\w.+-]+@[\w-]+\.[\w.-]+$", mail) else None,
                      "action_url": f"mailto:{mail}" if re.match(r"^[\w.+-]+@khu\.ac\.kr$", mail) else None, "extension": None}
                c["channels"].append(ch)
                c["evidence"].extend({"field": f"channels.{ch['id']}", **e} for e in ev)
            n_p = sum(1 for ch in c["channels"] if ch["kind"]["code"] == "phone")
            for raw in p.get("phones", []):
                camp = p["campus"]
                chans = phone_channels(c["id"], raw, camp, "phone", n_p)
                n_p += len(chans)
                c["channels"].extend(chans)
                c["evidence"].extend({"field": f"channels.{ch['id']}", **e} for ch in chans for e in ev)
            n_f = sum(1 for ch in c["channels"] if ch["kind"]["code"] == "fax")
            for raw in p.get("faxes", []):
                chans = phone_channels(c["id"], raw, p["campus"], "fax", n_f)
                n_f += len(chans)
                c["channels"].extend(chans)
                c["evidence"].extend({"field": f"channels.{ch['id']}", **e} for ch in chans for e in ev)
            n_w = sum(1 for ch in c["channels"] if ch["kind"]["code"] == "website")
            for i, w in enumerate(p.get("links", []), n_w):
                ch = {"id": f"{c['id']}-w{i}", "kind": {"code": "website", "label": "공식 링크"}, "display_value": w["label"],
                      "value": w["url"], "action_url": w["url"], "extension": None}
                c["channels"].append(ch)
                c["evidence"].extend({"field": f"channels.{ch['id']}", **e} for e in ev)
            if p.get("verification"):
                c["verification"] = {**VER[p["verification"]], "verified_at": observed_at}
            if p.get("note"):
                c["note"] = p["note"]
    added = 0
    for x in enrich.get("extra_contacts", []):
        camp = CAMPUS[x["campus"]]
        cid = "kx-" + hid(x["campus"], *x["path"], x["service_name"])
        ev = [{"url": e["url"], "source_name": e["source_name"], "observed_at": observed_at} for e in x.get("evidence", [])]
        chans: list[dict] = []
        for i, raw in enumerate(x.get("phones", [])):
            chans.extend(phone_channels(cid, raw, x["campus"], "phone", sum(1 for c in chans if c["kind"]["code"] == "phone")))
        for raw in x.get("faxes", []):
            chans.extend(phone_channels(cid, raw, x["campus"], "fax", sum(1 for c in chans if c["kind"]["code"] == "fax")))
        for i, mail in enumerate(x.get("emails", [])):
            chans.append({"id": f"{cid}-e{i}", "kind": {"code": "email", "label": "이메일"}, "display_value": mail, "value": mail,
                          "action_url": f"mailto:{mail}", "extension": None})
        for i, w in enumerate(x.get("links", [])):
            chans.append({"id": f"{cid}-w{i}", "kind": {"code": "website", "label": "공식 링크"}, "display_value": w["label"],
                          "value": w["url"], "action_url": w["url"], "extension": None})
        ctype = x.get("type", "office")
        contacts.append({
            "id": cid, "kind": "contact",
            "organization": {"id": "org-" + hid(x["campus"], *x["path"][2:], n=8), "name": x["path"][-1], "path": x["path"],
                             "type": {"code": ctype, "label": TYPE_LABEL[ctype]}},
            "campuses": [camp] + ([CAMPUS["global" if x["campus"] == "seoul" else "seoul"]] if x.get("both_campuses") else []),
            "service_name": x["service_name"], "channels": chans,
            "location": x.get("location"), "office_hours": x.get("office_hours"), "official_url": x.get("official_url"),
            "verification": {**VER[x.get("verification", "verified")], "verified_at": observed_at},
            "evidence": [{"field": f"channels.{c['id']}", **e} for c in chans for e in ev]
                        + [{"field": f, **e} for f in ("location", "office_hours", "official_url") if x.get(f) for e in ev],
            "source": {"directory_section": None, "directory_org": None, "group": None, "label": x["service_name"], "raw": None},
            **({"note": x["note"]} if x.get("note") else {}),
        })
        added += 1
    return applied, added


# --------------------------------------------------------------------------- main
def main() -> None:
    observed_at = (RAW / "observed_at.txt").read_text(encoding="utf-8").strip()
    parsed, stats = {}, {}
    for campus, fname in (("seoul", "khu_phone_directory_seoul.txt"), ("global", "khu_phone_directory_global.txt")):
        orgs, unmatched = parse_directory(RAW / fname, campus)
        parsed[campus] = orgs
        stats[campus] = {"orgs": len(orgs), "entries": sum(len(o["entries"]) for o in orgs), "unmatched": unmatched}
        for u in unmatched:
            print(f"  [warn] {campus} unmatched line {u}", file=sys.stderr)
    (OUT / "phone_directory.json").write_text(json.dumps({
        "source": DIRECTORY_URL, "observed_at": observed_at,
        "dial_rules": {
            "seoul": {"main": "02-961-0114", "external": "02-961-내선번호", "from_global": "4 + 내선번호", "from_medical": "6 + 내선번호"},
            "global": {"main": "031-201-3114", "external": "031-201-내선번호", "from_seoul": "3 + 내선번호"},
        },
        "campuses": parsed,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    contacts = build_contacts(parsed, observed_at)
    enrich = json.loads((OUT / "enrichment.json").read_text(encoding="utf-8"))
    applied, added = apply_enrichment(contacts, enrich, observed_at)
    contacts.sort(key=lambda c: (c["campuses"][0]["id"], c["organization"]["path"], c["service_name"], c["id"]))

    summary = {
        "generated_at": observed_at,
        "contacts": len(contacts),
        "by_campus": dict(Counter(c["campuses"][0]["name"] for c in contacts)),
        "by_type": dict(Counter(c["organization"]["type"]["code"] for c in contacts)),
        "with_office_hours": sum(1 for c in contacts if c["office_hours"]),
        "with_location": sum(1 for c in contacts if c["location"]),
        "with_email": sum(1 for c in contacts if any(ch["kind"]["code"] == "email" for ch in c["channels"])),
        "conflicts": sum(1 for c in contacts if c["verification"]["code"] == "conflict"),
        "directory": {k: {"orgs": v["orgs"], "entries": v["entries"]} for k, v in stats.items()},
        "enrichment": {"patches_applied": applied, "extra_contacts": added},
    }
    (OUT / "contacts.json").write_text(json.dumps({"summary": summary, "data": contacts}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
