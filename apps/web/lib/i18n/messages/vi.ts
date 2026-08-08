import type { Messages } from "./en";

/** Vietnamese. Verbs for actions, nouns for places — the register a Vietnamese
 *  product interface actually uses, not a gloss of the English. */
export const vi: Messages = {
  app: {
    name: "TrendRelay",
    tagline: "Khám phá xu hướng. Nghiên cứu điều đáng giá.",
  },

  nav: {
    discover: "Khám phá",
    library: "Thư viện",
    studio: "Xưởng dựng",
    campaigns: "Chiến dịch",
    publish: "Đăng bài",
    catalog: "Danh mục",
    attribution: "Phân bổ",
    opportunities: "Cơ hội",
    tools: "Công cụ",
    workspaces: "Không gian làm việc",
    about: "Giới thiệu",
    signIn: "Đăng nhập",
    signOut: "Đăng xuất",
    language: "Ngôn ngữ",
    chooseLanguage: "Chọn ngôn ngữ",
  },

  common: {
    save: "Lưu",
    cancel: "Hủy",
    close: "Đóng",
    delete: "Xóa",
    confirm: "Xác nhận",
    retry: "Thử lại",
    reload: "Tải lại",
    refresh: "Làm mới",
    loading: "Đang tải…",
    search: "Tìm kiếm",
    filter: "Lọc",
    clear: "Xóa bộ lọc",
    selectAll: "Chọn tất cả",
    none: "Không có",
    all: "Tất cả",
    open: "Mở",
    download: "Tải xuống",
    preview: "Xem trước",
    apply: "Áp dụng",
    back: "Quay lại",
    next: "Tiếp theo",
    yes: "Có",
    no: "Không",
    optional: "Không bắt buộc",
    required: "Bắt buộc",
    unavailable: "Không khả dụng",
    comingSoon: "Sắp có",
  },

  status: {
    queued: "Đang chờ",
    running: "Đang chạy",
    succeeded: "Thành công",
    failed: "Thất bại",
    partial: "Hoàn tất một phần",
    cancelled: "Đã hủy",
    ready: "Sẵn sàng",
    setupRequired: "Cần thiết lập",
  },

  workspace: {
    loading: "Đang tải không gian làm việc…",
    loadingHelp:
      "Việc này chỉ mất một lát. Nếu lâu hơn, có thể API chưa chạy.",
    none: "Chưa có không gian làm việc",
    select: "Không gian làm việc",
  },

  discover: {
    title: "Tìm kiếm nổi bật trên Douyin",
    subtitleEmpty:
      "Những gì đang thịnh hành trên Douyin ngay lúc này, từ phiên đã kết nối của bạn.",
    read: "Xem bảng xếp hạng",
    reading: "Đang đọc…",
    gallery: "Dạng lưới",
    list: "Dạng danh sách",
    boardLayout: "Bố cục bảng",
    downloadPerTopic: "Tải {count} video mỗi chủ đề",
    downloadCount: "Tải {count} video",
    browse: "Xem trên Douyin",
    queueing: "Đang thêm vào hàng đợi…",
    heat: "{value} lượt quan tâm",
    views: "{value} lượt xem",
    emptyBoard: "Bảng xếp hạng đang trống. Vui lòng thử lại sau ít phút.",
    topicQueued:
      "Đã thêm {count, plural, other {# video}} cho “{term}” vào hàng đợi. Theo dõi ở mục Tải xuống.",
    topicFailed: "Không lấy được chủ đề này.",
    boardTermsNeedNoAccount: "Các từ khóa ở bảng trên thì không cần tài khoản.",
    connectAccount: "Kết nối tài khoản trong Công cụ",
  },

  downloads: {
    heading: "Tải video từ Douyin",
    eyebrowAcquisition: "THU THẬP NỘI DUNG",
    intro:
      "Dán liên kết video, trang cá nhân hoặc bộ sưu tập. TrendRelay sẽ tải ngầm và thêm tệp vào thư viện của bạn.",
    signInPrompt: "Đăng nhập để quản lý nội dung.",
    signInIntro:
      "Tải video nguồn, dựng clip và đăng bài đã duyệt — tất cả trong một không gian làm việc.",
    tryAgain: "Thử lại",
    workspaceFirst: "Hãy tạo không gian làm việc trước",
    workspaceOwns:
      "Không gian làm việc quản lý nội dung, phê duyệt và lịch sử đăng bài.",
    createWorkspace: "Tạo không gian làm việc",
    step: "BƯỚC {number}",
    addLinks: "Thêm liên kết Douyin",
    addLinksHelp: "Dán tin nhắn chia sẻ đã sao chép, hoặc mỗi dòng một liên kết.",
    linksLabel: "Liên kết Douyin",
    pasteFromClipboard: "Dán từ bộ nhớ tạm",
    readyToDownload: "Sẵn sàng tải",
    remove: "Xóa",
    installProvider: "Cài đặt trình tải Douyin",
    installProviderHelp: "Bật trình cung cấp được quản lý một lần, rồi quay lại đây.",
    openTools: "Mở Công cụ",
    refreshSession: "Làm mới phiên Douyin",
    refreshSessionHelp:
      "Phiên Douyin được lưu trên máy bạn. Chỉ làm mới khi việc tải không còn hoạt động.",
    options: "Tùy chọn tải",
    fromProfiles: "Nội dung từ trang cá nhân",
    publishedPosts: "Bài đã đăng",
    likedVideos: "Video đã thích",
    collections: "Bộ sưu tập",
    musicVideos: "Video nhạc",
    perSource: "Số video mỗi nguồn",
    whatToFetch: "Nội dung cần tải",
    whatToFetchHelp:
      "Mặc định là tải tất cả video. TrendRelay tiếp tục duyệt qua nguồn và bỏ qua tệp đã tải. Bỏ chọn một mục nghĩa là không bao giờ yêu cầu nó, thay vì tải về rồi bỏ đi.",
    authorisedOnly:
      "Chỉ tải nội dung mà bạn có quyền lưu giữ và sử dụng lại.",
    heading2: "Danh sách tải",
    autoUpdate: "Các lô đang chạy tự động cập nhật sau vài giây.",
    openSource: "Mở nguồn",
    openFolder: "Mở thư mục",
    openLibrary: "Mở thư viện",
    noneSaved: "Không có tệp nào được lưu",
    reuseLinks: "Làm mới phiên Douyin, rồi dùng lại các liên kết này.",
    openInLibrary: "Mở trong Thư viện",
    plan: "Lên kế hoạch",
    detectedSources: "Nguồn Douyin đã nhận diện",
    refreshList: "Làm mới danh sách tải",
    filter: "Lọc danh sách tải",
    addCreatorProfile:
      "Thêm trang cá nhân Douyin của nhà sáng tạo vào ô liên kết để tải toàn bộ nội dung của họ",
  },

  research: {
    results: "Kết quả",
    relevance: "Độ liên quan",
    noSignals: "Chưa có tín hiệu nào thuộc loại này.",
    recent: "Nghiên cứu gần đây",
    noImage: "không có ảnh",
    tiktokRegion: "Khu vực TikTok",
    tiktokPeriod: "Khoảng thời gian TikTok",
    perTopicHelp: "Số video tải về cho mỗi chủ đề",
    openTermOnDouyin: "Mở từ khóa này trên Douyin",
  },

  effects: {
    title: "Chỉnh sửa",
    unavailable: "Không khả dụng trên máy này",
    licenceRequired: "Cần quyết định về giấy phép trước khi chạy",
    installHint: "Cài đặt tiện ích bổ sung để bật tính năng này",
  },

  language: {
    switched: "Đã chuyển sang {language}",
  },
};
