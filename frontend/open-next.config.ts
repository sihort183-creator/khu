// Cloudflare Workers 로 Next 를 올리기 위한 설정.
// 캐시 저장소는 두지 않는다. 화면이 쓰는 자료는 모두 조회 서버(정적 JSON)에서 오고,
// 캐시는 그쪽 가장자리에서 이미 걸린다. 여기에 또 두면 값만 늘고 얻는 게 없다.
import { defineCloudflareConfig } from "@opennextjs/cloudflare";

export default defineCloudflareConfig();
