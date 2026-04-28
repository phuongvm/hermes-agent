# Báo Cáo Nghiên Cứu: Kiến Trúc Dịch Vụ Bộ Nhớ Honcho

## 1. Nền Tảng Triết Lý (Philosophical Foundations)
Honcho tạo ra sự khác biệt so với hệ thống RAG (Retrieval-Augmented Generation) thụ động truyền thống bằng cách đi tiên phong trong phương pháp tiếp cận **bộ nhớ chủ động, định hướng suy luận (active, reasoning-driven memory)**. Mục tiêu của nó không chỉ đơn thuần là trích xuất sự kiện, mà là tạo ra "mô phỏng trạng thái" (stateful simulation) thực sự thông qua việc xây dựng các mô hình tâm lý mạch lạc cho các thực thể.
- **Logic Hình Thức (Formal Logic) vs. Văn Bản Có Thể Xảy Ra (Plausible Text)**: Honcho tận dụng các mô hình ngôn ngữ tùy chỉnh được huấn luyện bằng logic hình thức để trích xuất các kiến thức tiềm ẩn và đưa ra các kết luận suy diễn, thay vì chỉ làm nổi bật các đoạn văn bản tương tự nhau.
- **Mô Hình Lấy Peer Làm Trung Tâm (The Peer-Centric Paradigm)**: Honcho từ bỏ cấu trúc "người dùng vs. trợ lý" truyền thống. Tất cả các thực thể—con người, AI agent, hay các nhóm—đều được đối xử bình đẳng như những "Peers" (Người dùng ngang hàng). Điều này cho phép các hệ thống multi-agent mô phỏng và suy luận về các agent khác chính xác theo cách chúng mô phỏng người dùng con người.

```mermaid
sequenceDiagram
    participant HP as Human Peer (Người)
    participant S as Session (Phiên)
    participant AP as AI Agent Peer (AI)
    participant Honcho
    
    Note over HP,AP: Mô Hình Peer-Centric: Mọi thực thể là Peer bình đẳng
    HP->>S: Gửi Tin nhắn
    AP->>S: Gửi Tin nhắn
    AP->>Honcho: Truy vấn trạng thái của Human Peer
    Honcho-->>AP: Trả về Peer Card & Mô Hình Tâm Lý
```

## 2. Kiến Trúc Cốt Lõi (Core Architecture)
Hệ thống sử dụng một mô hình dữ liệu phân cấp (`Workspaces -> Peers -> Sessions -> Messages`) và chia các hoạt động thành một tầng lưu trữ bộ nhớ tiêu chuẩn và một Tầng Suy Luận (Reasoning Layer) chạy ngầm. Tầng Suy Luận bao gồm ba agent chính:

```mermaid
sequenceDiagram
    participant W as Workspace (Không gian làm việc)
    participant P as Peer (Thực thể)
    participant S as Session (Phiên trò chuyện)
    participant M as Message (Tin nhắn)
    
    W->>P: Tạo/Định danh Peer
    P->>S: Khởi tạo Session
    S->>M: Ghi nhận Message
    M-->>Honcho: Kích hoạt tầng suy luận chạy ngầm
```

### The Deriver (Bộ Thu Thập & Khai Phá)
- Đóng vai trò là động cơ suy luận tức thì cho dữ liệu đầu vào.
- Xử lý các tin nhắn một cách bất đồng bộ thông qua hàng đợi (queue).
- Sử dụng **token batching** (kích hoạt suy luận mỗi khi gom đủ ~1,000 tokens) để đảm bảo có đủ ngữ cảnh có ý nghĩa trong khi vẫn giữ chi phí API ở mức thấp.
- Trích xuất các sự thật rõ ràng (explicit facts) và suy diễn ra các hiểu biết tiềm ẩn (unstated deductive insights).

```mermaid
sequenceDiagram
    participant S as Session
    participant Q as Message Queue (Hàng Đợi)
    participant D as The Deriver
    participant DB as Knowledge Base (Cơ Sở Kiến Thức)
    
    S->>Q: Tin nhắn đầu vào
    Note over Q: Tích lũy ~1,000 tokens
    Q->>D: Kích hoạt xử lý lô bất đồng bộ
    D->>D: Chạy bước Suy Luận Logic Hình Thức
    D->>DB: Trích xuất & Lưu Explicit Facts
    D->>DB: Suy diễn & Lưu Latent Insights
```

### The Dreamer (Bộ Củng Cố)
- Là một agent bảo trì chạy ngầm định kỳ mỗi 8 đến 24 giờ.
- Thực hiện một chuyến "đi dạo ngẫu nhiên" (random walk) qua các quan sát về peer để củng cố bộ nhớ: hợp nhất những thông tin dư thừa, xóa bỏ các thông tin lỗi thời hoặc mâu thuẫn, và tổng hợp các sự kiện cụ thể thành những quy luật rộng lớn hơn (thông qua quy nạp và suy luận giả thuyết).
- Đầu ra chính của nó là **Peer Card**, một hồ sơ tiểu sử siêu nén (bị giới hạn cứng ở 40 sự kiện) được tiêm trực tiếp vào prompt của agent nhằm bỏ qua độ trễ của quá trình truy xuất (retrieval latency).

```mermaid
sequenceDiagram
    participant Timer as Cron (8-24h)
    participant Dr as The Dreamer
    participant DB as Knowledge Base (Facts)
    participant PC as Peer Card (Hồ sơ Peer)
    
    Timer->>Dr: Kích hoạt chu kỳ củng cố
    Dr->>DB: Đọc ngẫu nhiên (Random Walk) các Facts
    Dr->>Dr: Hợp nhất trùng lặp & Giải quyết mâu thuẫn
    Dr->>Dr: Tổng hợp thành quy luật (Quy nạp/Giả thuyết)
    Dr->>DB: Xóa các Fact lỗi thời/mâu thuẫn
    Dr->>PC: Cập nhật Peer Card (Tối đa 40 Facts cốt lõi)
```

### The Dialectic (Bộ Truy Xuất & Tổng Hợp)
- Là một API truy xuất ngôn ngữ tự nhiên đóng vai trò "Nhà Tiên Tri" (Oracle) để truy vấn bộ nhớ của peer.
- Sử dụng **Suy Luận Nhiều Bước (Multi-Pass Reasoning)**: Bước 0 (Đánh giá), Bước 1 (Tự Kiểm Toán), và Bước 2 (Hòa giải các mâu thuẫn).
- Tự động chuyển đổi giữa chế độ "Cold Start" (tiểu sử rộng) và "Warm Session" (thu hẹp trong ngữ cảnh gần đây).
- Đầu ra là một **Dialectic Supplement**—phần suy luận được tổng hợp bởi LLM theo thời gian thực về các nhu cầu hiện tại của người dùng—được tiêm vào cùng với ngữ cảnh gốc trong mọi lượt hội thoại.

```mermaid
sequenceDiagram
    participant Agent
    participant Dialectic as The Dialectic
    participant DB as Knowledge Base
    
    Agent->>Dialectic: Truy vấn Ngôn Ngữ Tự Nhiên
    Dialectic->>DB: Pass 0: Đánh giá trạng thái & Tiểu sử chung
    DB-->>Dialectic: Ngữ cảnh ban đầu
    Dialectic->>Dialectic: Pass 1: Tự Kiểm Toán (Xác định lỗ hổng)
    Dialectic->>DB: Truy vấn các Fact lịch sử cụ thể
    DB-->>Dialectic: Ngữ cảnh cụ thể
    Dialectic->>Dialectic: Pass 2: Hòa giải (Giải quyết mâu thuẫn)
    Dialectic-->>Agent: Trả về 'Dialectic Supplement' (Ngữ Cảnh Chuyên Biệt)
```

## 3. Tích Hợp Công Cụ Honcho MCP
Máy chủ Honcho Model Context Protocol (MCP) cung cấp cho các agent khả năng thao tác trực tiếp lên tầng bộ nhớ:

- `honcho_context`: Truy xuất toàn bộ biểu diễn người dùng xuyên suốt các phiên (tóm tắt, peer cards, các quan sát liên quan).
- `honcho_ask`: Công cụ Hỏi-Đáp hỗ trợ bởi LLM tận dụng API Dialectic. Hỗ trợ cấu hình độ sâu của suy luận (nhanh vs. chi tiết).
- `honcho_conclude`: Cho phép agent lưu rõ ràng một insight hoặc một sự kiện mới dưới dạng "kết luận" (conclusion).
- `honcho_profile`: Truy xuất hoặc cập nhật "peer card" (hồ sơ tiểu sử) của người dùng.
- `honcho_search_conclusions`: Thực hiện tìm kiếm ngữ nghĩa trên các insight đã được suy luận để thu hồi sự kiện với độ trung thực cao.
- `honcho_search_messages`: Truy vấn các tin nhắn lịch sử trong phiên (có thể lọc theo ngày tháng và người gửi).
- `get_config` / `set_config`: Các công cụ tiện ích để lập trình kiểm tra hoặc sửa đổi các cấu hình bộ nhớ.

```mermaid
sequenceDiagram
    participant Agent
    participant MCP as Máy chủ Honcho MCP
    participant Backend as Honcho API
    
    Agent->>MCP: Gọi honcho_ask(query)
    MCP->>Backend: Thực thi Dialectic API
    Backend-->>MCP: Nhận Dialectic Supplement
    MCP-->>Agent: Trả về Ngữ cảnh
    
    Agent->>MCP: Gọi honcho_conclude(insight)
    MCP->>Backend: Bơm Fact mới một cách rõ ràng
    Backend-->>MCP: Thành công
    MCP-->>Agent: Xác nhận đã lưu
    
    Agent->>MCP: Gọi honcho_profile()
    MCP->>Backend: Lấy Peer Card
    Backend-->>MCP: Nhận Peer Card (40 facts)
    MCP-->>Agent: Trả về Hồ sơ (Profile)
```

## 4. Biểu Đồ Trực Quan

### 4.1 Bản Đồ Toàn Cảnh (Comprehensive Honcho Master Pipeline)
Biểu đồ này trực quan hóa cách mà Nền Tảng Triết Lý (Peer-Centric), Kiến Trúc Dữ Liệu, Động Cơ Suy Luận Chủ Động (Deriver, Dreamer, Dialectic) và Tích Hợp Công Cụ MCP được kết nối vào một luồng thống nhất.

```mermaid
flowchart TB
    %% Styles
    classDef peer fill:#3b82f6,stroke:#1e40af,color:white,stroke-width:2px,rx:10px
    classDef mcp fill:#8b5cf6,stroke:#5b21b6,color:white,stroke-width:2px,rx:10px
    classDef storage fill:#10b981,stroke:#047857,color:white,stroke-width:2px
    classDef agent fill:#f59e0b,stroke:#b45309,color:white,stroke-width:2px,rx:10px
    classDef output fill:#ef4444,stroke:#991b1b,color:white,stroke-width:2px

    subgraph 1_Peer_Centric_Environment [1. Triết Lý: Môi Trường Lấy Peer Làm Trung Tâm]
        H1[Human Peer - Người]:::peer
        A1[AI Agent Peer - AI]:::peer
    end

    subgraph 2_Honcho_MCP_Tools [2. Tích Hợp Công Cụ Honcho MCP]
        direction LR
        T_Ask[honcho_ask]:::mcp
        T_Context[honcho_context <br> honcho_search_*]:::mcp
        T_Conclude[honcho_conclude]:::mcp
        T_Profile[honcho_profile]:::mcp
    end

    H1 <-->|Tương tác với| A1
    A1 <-->|Thực thi| 2_Honcho_MCP_Tools

    subgraph 3_Honcho_Backend [3. Kiến Trúc Cốt Lõi & Suy Luận]
        direction TB
        
        subgraph Data_Storage [Phân Cấp Lưu Trữ Dữ Liệu]
            direction LR
            WS[Workspace] --> P[Peer] --> S[Session] --> M[Messages]
        end

        subgraph Reasoning_Engine [Động Cơ Suy Luận Chủ Động]
            direction TB
            Deriver[The Deriver <br> Thu thập & Khai Phá]:::agent
            Dreamer[The Dreamer <br> Củng Cố Định Kỳ]:::agent
            Dialectic[The Dialectic <br> Oracle Truy Xuất NL]:::agent
        end
        
        M -->|Hàng Đợi Async <br> ~1000 tokens| Deriver
        Deriver -->|Khai Phá Logic| Facts[(Kết Luận Đã Suy Diễn)]:::storage
        
        Facts -.->|Chu kỳ 8-24h <br> Random Walk| Dreamer
        Dreamer -->|Tổng hợp| PCard[Peer Card <br> Max 40 Facts]:::storage
        
        Facts --> Dialectic
        PCard --> Dialectic
    end

    %% MCP to Backend connections
    T_Ask -->|Truy vấn NL| Dialectic
    T_Context -.->|Đọc trực tiếp| Facts
    T_Conclude -->|Bơm thủ công| Facts
    T_Profile <-->|Đọc / Cập nhật| PCard

    Dialectic -->|Suy Luận Nhiều Bước <br> Đánh giá/Kiểm toán/Hòa giải| Supplement[Dialectic Supplement <br> Ngữ Cảnh Stateful]:::output
    Supplement -->|Tiêm vào Context| A1
```
