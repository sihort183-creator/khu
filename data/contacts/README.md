# 경희대 연락처 정적 데이터

기획서의 '연락처' 독립 분류를 위한 정적 자산. 2026-09-06 공식 원문에서 수집했고, 값이 원문에 없으면 비워 두었다(추정·생성 없음).

## 파일

| 파일 | 내용 |
|---|---|
| `contacts.json` | 최종 산출물. `data[]`는 [화면 연동 규격 초안 5절](../../KHU_API_CONTRACT_DRAFT.md) Contact 모델(`organization`, `campuses`, `service_name`, `channels`, `location`, `office_hours`, `official_url`, `verification`, `evidence`)을 따르고, 확장 필드로 `channels[].values`(범위 표기를 펼친 전체 번호), `source`(전화번호부 원문 위치), `note`(충돌·주의)를 둔다. `summary`에 집계. |
| `phone_directory.json` | 공식 교내 전화번호 안내를 구조화한 중간 산출물(캠퍼스 → 섹션 → 기관 → 업무 → 원문 번호). 외부 발신 규칙 포함. |
| `enrichment.json` | 기관 개별 페이지에서 확인한 위치·이메일·팩스·업무시간·공식 링크·SNS와 값 충돌 메모. 수기 관리. |
| `raw/khu_phone_directory_{seoul,global}.txt` | 전화번호부 페이지의 텍스트 스냅샷(재현용). `raw/observed_at.txt`는 관찰 시각(UTC). |
| `../../scripts/build_contacts.py` | 위 원문을 파싱·병합해 `contacts.json`, `phone_directory.json`을 다시 만든다. `python scripts/build_contacts.py` |

## 출처

1차 원문은 경희대 공식 [교내 전화번호 안내 서울](https://www.khu.ac.kr/kor/user/contents/view.do?menuNo=200091)·[국제](https://www.khu.ac.kr/kor/user/contents/view.do?menuNo=200092)이다. 두 페이지가 대학본부·대학·대학원·부속기관·부설연구소·학생회·편의시설·관리실·기타 전 섹션을 담고 있어 기획서가 열거한 기관(본부 부서, 단과대·학과, 사업단, 창업·학생생활기관, 학생자치기구)을 모두 덮는다. 페이지에 링크된 PDF(서울 2024.01, 국제 2022)는 내려받지 않았다.

위치·이메일·팩스·업무시간은 아래 개별 페이지에서만 채웠다(각 값의 `evidence`에 URL 기록).

- [대학본부 부서 안내](https://www.khu.ac.kr/kor/user/contents/view.do?menuNo=200058) — 본부 부서별 서울/국제 위치·전화·팩스·이메일
- [학생지원 기관 안내](https://www.khu.ac.kr/kor/user/contents/view.do?menuNo=200295), [학생지원센터](https://www.khu.ac.kr/kor/user/contents/view.do?menuNo=200172), [장애학생지원센터](https://www.khu.ac.kr/kor/user/contents/view.do?menuNo=200174), [미래인재센터](https://www.khu.ac.kr/kor/user/contents/view.do?menuNo=200180), [학생지원센터(장학) 담당부서](https://neoscholar.khu.ac.kr/janghak/user/contents/view.do?menuNo=12300061)
- 도서관 [이용시간(서울)](https://lib.khu.ac.kr/webcontent/info/40), [분관 안내(서울)](https://lib.khu.ac.kr/webcontent/info/61), [이용안내(국제)](https://lib.khu.ac.kr/webcontent/info/50), [조직 및 연락처](https://lib.khu.ac.kr/webcontent/info/57)
- 심리상담센터 [서울](http://counsell.khu.ac.kr/)·[국제](http://counsel.khu.ac.kr/), 건강센터 [서울](https://healthsc.khu.ac.kr/)·[국제](http://health.khu.ac.kr/), [인권센터 오시는 길](https://hrc.khu.ac.kr/hrc/user/contents/view.do?menuNo=12600044), [정보처 위치 및 연락처](https://com.khu.ac.kr/ois/user/contents/view.do?menuNo=14000028), [국제교류팀 Contact Us](https://oia.khu.ac.kr/oiak_kor/user/contents/view.do?menuNo=5800061)
- 기숙사 [세화원](https://sewhahall.khu.ac.kr/), [우정원](https://wjwdorm.khu.ac.kr/10/1020.kmc); 사업단 [SW중심대학](https://swedu.khu.ac.kr/01/04.php), [G-LAMP](https://com.khu.ac.kr/research/user/contents/view.do?menuNo=6400353), [RISE](https://com.khu.ac.kr/grise/user/contents/view.do?menuNo=21000055), [BK21 four 스마트관광](https://bk21four.khu.ac.kr/), [현장실습지원센터](https://intern.khu.ac.kr/index.do), 창업지원단 오시는 길 [서울](https://startup.khu.ac.kr/startup_kor/user/contents/view.do?menuNo=15200021)·[국제](https://startup.khu.ac.kr/startup_kor/user/contents/view.do?menuNo=15200022)
- 단과대 행정실: [정경대학](https://khsma.khu.ac.kr/khsma/user/contents/view.do?menuNo=500010), [경영대학](https://kbiz.khu.ac.kr/biz_kor/user/contents/view.do?menuNo=14500011), [이과대학](https://science.khu.ac.kr/science_kor/user/contents/view.do?menuNo=1200013), [외국어대학](https://foreign.khu.ac.kr/foreign_kor/user/contents/view.do?menuNo=16500008), [후마니타스칼리지](https://hc.khu.ac.kr/hc_kor/user/contents/view.do?menuNo=4300023)
- 학생자치 Instagram: 서울 총학 `@khu_58_mate`, 국제 총학 `@khu_58_route`, 중앙동아리연합회 `@khu_dongari`, 일반대학원 총학 서울 `@khugs_khu`·국제 `@khu_gsc` (검색 결과 노출 기준, 계정 페이지 본문은 열어 확인하지 못함)

## 번호 처리 규칙

- 원문 표기(`display_value`, 예 `0053~4, 0057`)는 그대로 두고, `value`에 외부 발신 번호 첫 값(서울 `02-961-내선`, 국제 `031-201-내선`), `values`에 범위를 펼친 전체 목록(최대 20개)을 둔다. `958-4603`처럼 국번이 다른 표기는 캠퍼스 지역번호만 붙이고, `1544-2828`·`02-744-8855`처럼 완전한 번호는 그대로 둔다.
- 4자리 내선만 있는 번호는 `extension`에 내선을 둔다(국제캠퍼스에서 서울은 `4+내선`, 서울에서 국제는 `3+내선`).
- FAX 행은 바로 앞 업무의 `fax` 채널로 붙이고 `tel:` 링크를 만들지 않는다. `mailto:`는 `@khu.ac.kr` 주소에만 만든다.
- 섹션 바로 아래 나열된 항목(서울 부설연구소·학생회·기타 등)은 각각 독립 기관으로 만들고 업무명을 `대표 문의`로 둔다. 단과대 아래 학과·학부·전공 표기는 `department` 유형의 별도 기관(`path`에 단과대 포함), 업무명은 `학과 사무실`.

## 집계 (2026-09-06 빌드)

| 항목 | 값 |
|---|---|
| 연락처 레코드 | 983 (서울 508, 국제 475) |
| 기관 수 | 426 |
| 유형 | 행정부서 393, 부속·연구기관 305, 단과대·대학원 176, 학과 81, 학생자치 28 |
| 위치 있음 | 90 |
| 이메일 있음 | 106 |
| 업무시간 있음 | 7 |
| 충돌 검토(`conflict`) | 6 |

## 한계와 후속

- **업무시간**: 경희대 기관 대부분이 홈페이지에 업무시간을 적지 않는다. 확인된 것은 심리상담센터(서울) `09:00~17:30 (점심 12:00~13:00)`와 도서관 자료실·열람실·분관 이용시간뿐이다. 학과 사무실·행정실은 원문 근거가 없어 전부 `null`이며, 프론트는 '정보 없음'으로 표시해야 한다. 통상 `평일 09:00~17:30, 점심 12:00~13:00`이라는 관행은 근거가 없어 넣지 않았다.
- **충돌**: 교무처 학사지원팀 팩스, 국제교류팀 팩스, 학생복지 위치(서울), 장애학생지원센터 위치(국제), 창업보육센터 위치·번호(서울), 국제 산학협력단 팀 구성이 원문 간에 다르다. `note`에 두 값을 적고 위치는 비워 두었다.
- **광릉캠퍼스**: 전화번호 안내 페이지(menuNo=200090)에 표가 없다. 행정실 `031-570-7012~6`, `gip@khu.ac.kr`만 확인했고 데이터에 넣지 않았다.
- **전화번호부 최신성**: 페이지 표는 갱신일이 없고 첨부 PDF 연도는 서울 2024·국제 2022다. 조직 개편(예: 미래혁신원·LINC 3.0 명칭)으로 개별 페이지와 팀명이 다른 곳이 있다. 백엔드 계획의 7일 재확인 주기와 `stale` 처리를 그대로 적용하면 된다.
- 개인 이름·개인 휴대전화는 수집하지 않았다. 편의시설·관리실·입점 상가도 원문에 있어 포함했으나 프론트 기본 목록에서는 유형·섹션으로 걸러 낮은 우선순위로 두는 것을 권한다(`source.directory_section`으로 구분 가능).
