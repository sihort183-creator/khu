/**
 * 검색에 쓰는 글자 고르기. 검색·바로가기 카드가 **같은 규칙**을 보게 하려고 따로 둔다.
 *
 * lib/api.ts 안에 있던 것을 옮겼다(2026-09-14). 카드도 검색어를 맞춰 봐야 하는데
 * api.ts 를 부르면 두 파일이 서로를 가리키게 되고, 규칙을 한 벌 더 적으면 가운뎃점
 * 네 가지를 다시 빠뜨린다. api.ts 는 여기서 받아 그대로 다시 내보낸다.
 */

/**
 * 검색어와 공지 글자를 맞춰 볼 수 있는 꼴로 고친다.
 *
 * NFKC → 소문자 → 숫자·영문·한글이 **아닌** 것은 전부 공백 → 공백 정리.
 * "지울 구두점을 늘어놓는" 대신 "남길 글자만 정하는" 방식이라 빠뜨릴 구두점이 없다.
 * 공지 제목에는 가운뎃점만 해도 `･`(U+FF65) `·` `∙` `・` 네 가지가 섞여 있고 대괄호·물결·
 * 하이픈도 제각각이다. 이 한 줄이 그 문제를 통째로 없앤다.
 */
export function normalizeForSearch(value: string): string {
  return value
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[^0-9a-z가-힣]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * 정규화한 뒤 공백까지 없앤 붙여쓰기 판.
 *
 * `학석사연계` 로 쳐도 `학･석사연계` 가 잡히는 자리다. 낱말 경계를 잃으므로 정규화 판보다
 * 헐거운 짝(`이전 과정` → `이전과정` 안의 `전과`)도 통과한다. 그래서 점수를 매길 때는
 * 정규화 판에서 맞은 것을 위로 올리고 붙여쓰기 판에서만 맞은 것은 깎는다(COMPACT_ONLY).
 */
export function compactForSearch(value: string): string {
  return normalizeForSearch(value).replace(/\s+/g, "");
}
