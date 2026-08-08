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
