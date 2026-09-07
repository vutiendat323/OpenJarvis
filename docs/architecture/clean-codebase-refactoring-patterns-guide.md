# Hướng Dẫn Chuẩn Dành Cho AI Coding Agent: Refactor Codebase Clean Theo Refactoring Patterns & Clean Architecture

> **Phiên bản:** 1.0.0  
> **Đối tượng áp dụng:** Autonomous AI Coding Agents, Pair-programming AI Agents, Code Review Subagents  
> **Mục tiêu:** Cung cấp bộ quy chuẩn tối cao và hướng dẫn thực thi chuẩn xác cho AI Agent khi thực hiện tái cấu trúc mã nguồn (Refactoring). Đảm bảo mã sau refactor đạt chuẩn Clean Architecture và SOLID, không phát sinh lỗi tiềm ẩn (zero regression), phẫu thuật chính xác (surgical precision) và tuyệt đối không phỏng đoán hay phức tạp hóa quá mức (Simplicity First).

---

## Mục Lục
1. [Phần 1: Triết Lý & Giới Hạn Refactor Cho AI Agent (Philosophy & Agent Boundaries)](#phần-1-triết-lý--giới-hạn-refactor-cho-ai-agent-philosophy--agent-boundaries)
2. [Phần 2: Danh Mục Code Smells & Bộ Refactoring Patterns Chuẩn (Smells & Patterns Catalog)](#phần-2-danh-mục-code-smells--bộ-refactoring-patterns-chuẩn-smells--patterns-catalog)
3. [Phần 3: Quy Trình 5 Bước Thực Hiện Refactor Chuẩn Hóa (The 5-Step Refactoring Protocol)](#phần-3-quy-trình-5-bước-thực-hiện-refactor-chuẩn-hóa-the-5-step-refactoring-protocol)
4. [Phần 4: Các Anti-Patterns AI Agent Hay Mắc Phải Khi Refactor (AI Refactoring Pitfalls)](#phần-4-các-anti-patterns-ai-agent-hay-mắc-phải-khi-refactor-ai-refactoring-pitfalls)
5. [Phần 5: Verification Checklist Trước Khi Tuyên Bố Hoàn Thành (Pre-Completion Checklist)](#phần-5-verification-checklist-trước-khi-tuyên-bố-hoàn-thành-pre-completion-checklist)

---

## Phần 1: Triết Lý & Giới Hạn Refactor Cho AI Agent (Philosophy & Agent Boundaries)

### 1.1. Định Nghĩa Cốt Lõi Về Refactoring
Theo định nghĩa kinh điển của Martin Fowler:
> *"Refactoring là một sự thay đổi có kỷ luật đối với cấu trúc bên trong của phần mềm nhằm làm cho nó dễ hiểu hơn và chi phí sửa đổi rẻ hơn, mà KHÔNG làm thay đổi hành vi có thể quan sát được bên ngoài của nó (observable behavior)."*

Đối với một AI Coding Agent, hành vi có thể quan sát được bao gồm:
- **Public API Contracts:** Tên hàm, tham số, kiểu trả về, cấu trúc dữ liệu xuất ra.
- **Side-effects:** Ghi log, tương tác I/O, database transactions, network calls, event triggers.
- **Exception & Error Contracts:** Các loại lỗi hoặc ngoại lệ được ném ra trong các tình huống biên cụ thể.
- **Timing & Resource Constraints:** Độ phức tạp thời gian/không gian không bị thoái lui (regression).

---

### 1.2. Ba Trụ Cột Kỷ Luật Dành Cho AI Agent

```
               ┌─────────────────────────────────────────┐
               │         AGENT REFACTORING TRIAD         │
               └────────────────────┬────────────────────┘
                                    │
         ┌──────────────────────────┼──────────────────────────┐
         │                          │                          │
         ▼                          ▼                          ▼
┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
│ Surgical Changes │      │ Simplicity First │      │   Goal-Driven    │
│  (Vết mổ tối vi) │      │  (Không suy đoán)│      │   Verification   │
│                  │      │                  │      │  (Kiểm chứng kép)│
└──────────────────┘      └──────────────────┘      └──────────────────┘
```

#### Trụ cột 1: Surgical Changes (Thay đổi chuẩn xác như phẫu thuật)
- **Chỉ chạm vào vùng được yêu cầu:** Một thay đổi chuẩn mực là thay đổi mà mọi dòng code trong diff đều có thể truy vết trực tiếp về mục tiêu refactor đã định.
- **Không "tiện tay dọn dẹp" (No drive-by cleanups):** Không tự ý sửa định dạng (formatting), căn dòng, sửa chính tả comment hay đổi cú pháp ở những khối code lân cận không liên quan.
- **Chỉ dọn rác do chính mình tạo ra:** Nếu quá trình refactor tạo ra các biến phụ, hàm helper tạm thời hoặc import không còn dùng nữa, agent phải xóa sạch chúng. Tuyệt đối không tự ý xóa dead code của người khác trừ khi được giao nhiệm vụ rõ ràng.

#### Trụ cột 2: Simplicity First & Anti-Speculation (Đơn giản tối thượng - Không phỏng đoán)
- **Không suy đoán tương lai (YAGNI - You Aren't Gonna Need It):** Không xây dựng hệ thống plugin, cấu hình dynamic, hay các tầng trừu tượng "để dự phòng cho tương lai".
- **Trừu tượng hóa tối thiểu:** Không tạo interface/abstraction cho các thành phần chỉ có một cài đặt (single-use implementation) trừ khi đó là ranh giới kiến trúc (boundary) bắt buộc theo Clean Architecture để test.
- **Tiêu chuẩn senior engineer:** Luôn tự chất vấn: *"Một kỹ sư giàu kinh nghiệm có thấy giải pháp này bị over-engineered không? Nếu đoạn code 200 dòng có thể rút ngắn xuống 50 dòng sáng sủa, hãy viết 50 dòng."*

#### Trụ cột 3: Goal-Driven Verification (Thực thi hướng mục tiêu kiểm chứng)
- Mọi hoạt động tái cấu trúc phải được kẹp giữa hai lần kiểm chứng: **Green trước khi chạm vào code** và **Green sau khi hoàn tất**.
- Refactor không phải là một hành động cảm tính, mà là một chuỗi các bước biến đổi bảo toàn trạng thái đúng (semantics-preserving transformations).

---

### 1.3. Ranh Giới Kiến Trúc & Bất Biến (Clean Architecture Invariants)

Khi áp dụng Clean Architecture vào quá trình refactor, agent phải tuân thủ nghiêm ngặt **Quy tắc phụ thuộc (Dependency Rule)**:

```
    ┌────────────────────────────────────────────────────────┐
    │  Frameworks & Drivers (Web, UI, Database, Devices)     │
    │    ┌──────────────────────────────────────────────┐    │
    │    │  Interface Adapters (Controllers, Gateways)  │    │
    │    │    ┌────────────────────────────────────┐    │    │
    │    │    │  Application Business Rules        │    │    │
    │    │    │  (Use Cases / Interactors)         │    │    │
    │    │    │    ┌──────────────────────────┐    │    │    │
    │    │    │    │  Enterprise Rules        │    │    │    │
    │    │    │    │  (Entities / Domain)     │    │    │    │
    │    │    │    └──────────────────────────┘    │    │    │
    │    │    └────────────────────────────────────┘    │    │
    │    └──────────────────────────────────────────────┘    │
    └────────────────────────────────────────────────────────┘
                       Dependencies point INWARD only ──►
```

1. **Chiều phụ thuộc chỉ hướng vào trong:**
   - Tầng `Domain (Entities)` không được import bất kỳ thư viện bên ngoài, ORM, framework hay adapter nào.
   - Tầng `Application (Use Cases)` chỉ phụ thuộc vào `Domain` và các cổng giao tiếp trừu tượng (Ports / Interfaces).
   - Tầng `Infrastructure` (Database, Web Framework, Third-party SDKs) cài đặt các interface do tầng bên trong định nghĩa.
2. **Không phá vỡ Ranh giới (Boundaries):** Khi refactor một use-case, nếu phát hiện use-case đó đang import trực tiếp driver database hoặc HTTP client cụ thể, agent phải áp dụng Dependency Inversion Principle (DIP) để tách ranh giới bằng Interface, không được "tiện tay" truyền trực tiếp connection object vào entity.

---

## Phần 2: Danh Mục Code Smells & Bộ Refactoring Patterns Chuẩn (Smells & Patterns Catalog)

### 2.1. Bảng Ánh Xạ Smell-to-Pattern Tổng Hợp

| STT | Code Smell Phát Hiện | Vi Phạm Nguyên Tắc | Refactoring Pattern Chuẩn | Mức Độ Ưu Tiên |
|:---:|:---|:---|:---|:---:|
| 1 | **Long Method / Bloated Function** (> 20 dòng, đa tầng trừu tượng) | SRP, Stepdown Rule | **Extract Method**, Compose Method | Cao |
| 2 | **Deeply Nested Conditionals** (Arrow anti-pattern, Cyclomatic > 10) | Readability, Cognitive Load | **Replace Nested Conditional with Guard Clauses** | Cực cao |
| 3 | **Type Code & Branching on Type** (`switch/case` hoặc `if/elif` theo type) | OCP (Open/Closed) | **Replace Conditional with Polymorphism / Strategy** | Cao |
| 4 | **Long Parameter List** (> 3 tham số rời rạc) | Maintainability, Clean API | **Introduce Parameter Object / Value Object** | Trung bình |
| 5 | **God Class / Bloated Class** (> 200 dòng, kiêm nhiệm nhiều vai trò) | SRP, Cohesion | **Extract Class / Class Decomposition** | Cực cao |
| 6 | **Feature Envy & Inappropriate Intimacy** (Method mê dữ liệu class khác) | Law of Demeter, Cohesion | **Move Method / Move Field** | Cao |
| 7 | **Primitive Obsession** (Dùng string/int cho logic miền phức tạp) | Type Safety, Encapsulation | **Replace Data Value with Object / Value Object** | Trung bình |
| 8 | **Tight Coupling to Infrastructure** (Business logic import DB/SDK cụ thể) | DIP (Dependency Inversion) | **Invert Dependencies via Interface / Adapter** | Cực cao |

---

### 2.2. Chi Tiết Các Mẫu Refactor Kinh Điển Kèm Code Mẫu Chuẩn

#### Pattern 1: Extract Method / Compose Method
* **Vấn đề (Smell):** Hàm quá dài, trộn lẫn việc kiểm tra dữ liệu, tính toán toán học và gửi thông báo. Người đọc không thể hiểu mục đích cấp cao mà bị sa vào chi tiết cụ thể.
* **Giải pháp:** Tách các đoạn logic thành các private helper functions độc lập, mỗi hàm làm đúng một việc và có tên thể hiện rõ ý định (intention-revealing name).

##### ❌ Before (Monolithic & Mixed Abstractions)
```python
def process_order(order: dict, user_id: str, db_conn) -> bool:
    # 1. Validation (20 lines)
    if not order.get("items") or len(order["items"]) == 0:
        raise ValueError("Order must contain at least one item.")
    if not user_id or not user_id.startswith("USR-"):
        raise ValueError("Invalid user identifier.")
    for item in order["items"]:
        if item.get("quantity", 0) <= 0 or item.get("price", 0) <= 0:
            raise ValueError(f"Invalid item details: {item}")

    # 2. Calculation & Discount (15 lines)
    subtotal = sum(i["price"] * i["quantity"] for i in order["items"])
    discount = 0.0
    if subtotal > 1000:
        discount = subtotal * 0.1
    elif subtotal > 500:
        discount = subtotal * 0.05
    total = subtotal - discount

    # 3. Persistence & Notification (15 lines)
    cursor = db_conn.cursor()
    cursor.execute("INSERT INTO orders (user_id, total) VALUES (%s, %s)", (user_id, total))
    db_conn.commit()
    return True
```

##### ✅ After (Composed Method & Clear Levels of Abstraction)
```python
def process_order(order: dict, user_id: str, db_conn) -> bool:
    """Hàm điều phối cấp cao tuân theo Stepdown Rule."""
    _validate_order_payload(order, user_id)
    total_amount = _calculate_final_total(order)
    _save_order(db_conn, user_id, total_amount)
    return True


def _validate_order_payload(order: dict, user_id: str) -> None:
    if not user_id or not user_id.startswith("USR-"):
        raise ValueError("Invalid user identifier.")
    if not order.get("items"):
        raise ValueError("Order must contain at least one item.")
    for item in order["items"]:
        if item.get("quantity", 0) <= 0 or item.get("price", 0) <= 0:
            raise ValueError(f"Invalid item details: {item}")


def _calculate_final_total(order: dict) -> float:
    subtotal = sum(i["price"] * i["quantity"] for i in order["items"])
    discount = _calculate_discount(subtotal)
    return subtotal - discount


def _calculate_discount(subtotal: float) -> float:
    if subtotal > 1000:
        return subtotal * 0.1
    if subtotal > 500:
        return subtotal * 0.05
    return 0.0


def _save_order(db_conn, user_id: str, total: float) -> None:
    cursor = db_conn.cursor()
    cursor.execute("INSERT INTO orders (user_id, total) VALUES (%s, %s)", (user_id, total))
    db_conn.commit()
```

---

#### Pattern 2: Guard Clauses (Early Return / Fail Fast)
* **Vấn đề (Smell):** Cấu trúc "mũi tên" (Arrow Anti-Pattern) do các câu lệnh `if/else` lồng nhau quá sâu. Nhánh xử lý chính (happy path) bị chôn vùi ở tận đáy lồng ghép.
* **Giải pháp:** Đảo ngược điều kiện lỗi, xử lý thất bại hoặc thoát hàm ngay lập tức ở đầu hàm (Fail Fast), đưa happy path về độ thụt lề cấp 0.

##### ❌ Before (Deeply Nested "Arrow" Anti-pattern)
```typescript
function processPayment(payment: PaymentRequest, account: UserAccount): PaymentResult {
    let result: PaymentResult;
    if (account != null) {
        if (account.isActive) {
            if (account.balance >= payment.amount) {
                if (payment.amount > 0) {
                    account.balance -= payment.amount;
                    result = { success: true, transactionId: generateId() };
                } else {
                    result = { success: false, error: "Invalid payment amount" };
                }
            } else {
                result = { success: false, error: "Insufficient funds" };
            }
        } else {
            result = { success: false, error: "Account is inactive" };
        }
    } else {
        result = { success: false, error: "Account not found" };
    }
    return result;
}
```

##### ✅ After (Linear Flow with Guard Clauses)
```typescript
function processPayment(payment: PaymentRequest, account: UserAccount): PaymentResult {
    if (!account) {
        return { success: false, error: "Account not found" };
    }
    if (!account.isActive) {
        return { success: false, error: "Account is inactive" };
    }
    if (payment.amount <= 0) {
        return { success: false, error: "Invalid payment amount" };
    }
    if (account.balance < payment.amount) {
        return { success: false, error: "Insufficient funds" };
    }

    // Happy path phẳng hoàn toàn
    account.balance -= payment.amount;
    return { success: true, transactionId: generateId() };
}
```

---

#### Pattern 3: Replace Conditional with Polymorphism / Strategy
* **Vấn đề (Smell):** Sử dụng các khối `switch/case` hoặc `if/elif` trải dài theo kiểu loại (Type Code). Mỗi khi thêm một loại mới, ta buộc phải mở code hiện tại ra để sửa, vi phạm trực tiếp OCP (Open/Closed Principle).
* **Giải pháp:** Định nghĩa một Strategy interface hoặc Base Class trừu tượng, đóng gói logic của từng trường hợp vào một concrete class riêng biệt.

##### ❌ Before (Type Branching violating Open/Closed)
```python
class TaxCalculator:
    def calculate_tax(self, order_type: str, amount: float) -> float:
        if order_type == "standard":
            return amount * 0.10
        elif order_type == "luxury":
            return amount * 0.25 + 50.0
        elif order_type == "essential":
            return 0.0
        elif order_type == "export":
            fee = 15.0 if amount > 200 else 5.0
            return amount * 0.02 + fee
        else:
            raise ValueError(f"Unknown order type: {order_type}")
```

##### ✅ After (Polymorphic Strategy Pattern)
```python
from abc import ABC, abstractmethod
from typing import Dict


class TaxStrategy(ABC):
    @abstractmethod
    def calculate(self, amount: float) -> float:
        pass


class StandardTax(TaxStrategy):
    def calculate(self, amount: float) -> float:
        return amount * 0.10


class LuxuryTax(TaxStrategy):
    def calculate(self, amount: float) -> float:
        return amount * 0.25 + 50.0


class EssentialTax(TaxStrategy):
    def calculate(self, amount: float) -> float:
        return 0.0


class ExportTax(TaxStrategy):
    def calculate(self, amount: float) -> float:
        fee = 15.0 if amount > 200 else 5.0
        return amount * 0.02 + fee


class TaxCalculator:
    """Được mở rộng thông qua đăng ký Strategy, đóng với việc sửa đổi mã nguồn."""

    def __init__(self, strategies: Dict[str, TaxStrategy]):
        self._strategies = strategies

    def calculate_tax(self, order_type: str, amount: float) -> float:
        strategy = self._strategies.get(order_type)
        if not strategy:
            raise ValueError(f"Unsupported order type: {order_type}")
        return strategy.calculate(amount)
```

---

#### Pattern 4: Introduce Parameter Object & Value Object
* **Vấn đề (Smell):** Danh sách tham số quá dài (Long Parameter List), dữ liệu rời rạc lặp đi lặp lại nhiều nơi (Data Clump), dùng primitive types (string, number) để biểu diễn các khái niệm nghiệp vụ có ràng buộc (Primitive Obsession).
* **Giải pháp:** Gom cụm các tham số có quan hệ chặt chẽ thành một Parameter Object hoặc tạo Value Object bất biến (Immutable Value Object) có khả năng tự validate tính toàn vẹn.

##### ❌ Before (Primitive Obsession & Parameter Clump)
```python
def register_employee(
    first_name: str,
    last_name: str,
    email: str,
    street: str,
    city: str,
    zip_code: str,
    base_salary: float,
    currency: str
) -> None:
    # Validate email thủ công ở nhiều hàm khác nhau
    if "@" not in email or "." not in email:
        raise ValueError("Invalid email")
    if base_salary < 0:
        raise ValueError("Salary cannot be negative")
    # ... logic đăng ký ...
```

##### ✅ After (Parameter Object & Encapsulated Value Objects)
```python
from dataclasses import dataclass
import re

@dataclass(frozen=True)
class Email:
    value: str

    def __post_init__(self):
        pattern = r"^[\w\.-]+@[\w\.-]+\.\w+$"
        if not re.match(pattern, self.value):
            raise ValueError(f"Invalid email address: {self.value}")


@dataclass(frozen=True)
class Address:
    street: str
    city: str
    zip_code: str


@dataclass(frozen=True)
class Money:
    amount: float
    currency: str

    def __post_init__(self):
        if self.amount < 0:
            raise ValueError("Amount cannot be negative.")


@dataclass(frozen=True)
class EmployeeRegistrationPayload:
    """Parameter Object gom nhóm thông tin cần thiết."""
    first_name: str
    last_name: str
    email: Email
    address: Address
    compensation: Money


def register_employee(payload: EmployeeRegistrationPayload) -> None:
    # Mọi dữ liệu đi vào đây đều đã được đảm bảo tính đúng đắn về mặt miền dữ liệu
    # ... logic đăng ký ...
    pass
```

---

#### Pattern 5: Class Decomposition & Dependency Inversion (Tách God Class & Đảo Ngược Phụ Thuộc)
* **Vấn đề (Smell):** Một class duy nhất vừa thực hiện validation, vừa mở trực tiếp connection đến database, vừa xử lý logic nghiệp vụ, vừa gửi email và ghi log. Khi có sự thay đổi ở database driver hoặc template email, toàn bộ class bị ảnh hưởng.
* **Giải pháp:** Tách class thành các component đơn trách nhiệm (SRP). Sử dụng Dependency Injection và Interfaces (DIP) để decoupling.

##### ❌ Before (Monolithic God Class)
```python
import sqlite3
import smtplib

class UserManager:
    def create_user(self, username: str, email: str, role: str):
        # 1. Validation
        if len(username) < 3:
            raise ValueError("Username too short")
        if "@" not in email:
            raise ValueError("Invalid email")

        # 2. Database persistence (Direct coupling)
        conn = sqlite3.connect("production.db")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users VALUES (?, ?, ?)", (username, email, role))
        conn.commit()

        # 3. Notification (Direct coupling)
        smtp = smtplib.SMTP("smtp.server.com")
        smtp.sendmail("admin@system.com", email, f"Welcome {username}!")

        # 4. Logging
        with open("app.log", "a") as f:
            f.write(f"User created: {username}\n")
```

##### ✅ After (Clean Architecture Layers with Injected Dependencies)
```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
import logging

# --- DOMAIN ENTITY ---
@dataclass(frozen=True)
class User:
    username: str
    email: str
    role: str


# --- DOMAIN INTERFACES (PORTS) ---
class UserRepository(ABC):
    @abstractmethod
    def save(self, user: User) -> None:
        pass


class NotificationService(ABC):
    @abstractmethod
    def notify_welcome(self, user: User) -> None:
        pass


# --- APPLICATION USE CASE ---
class CreateUserUseCase:
    """Tập trung duy nhất vào nghiệp vụ điều phối tạo User."""

    def __init__(
        self,
        repository: UserRepository,
        notifier: NotificationService,
        logger: logging.Logger
    ):
        self._repository = repository
        self._notifier = notifier
        self._logger = logger

    def execute(self, user: User) -> None:
        self._validate(user)
        self._repository.save(user)
        self._notifier.notify_welcome(user)
        self._logger.info("User created successfully: %s", user.username)

    def _validate(self, user: User) -> None:
        if len(user.username) < 3:
            raise ValueError("Username must have at least 3 characters")
        if "@" not in user.email:
            raise ValueError("Invalid email format")


# --- INFRASTRUCTURE ADAPTERS (Tách biệt hoàn toàn ở tầng ngoài) ---
class SqlUserRepository(UserRepository):
    def __init__(self, db_session):
        self._session = db_session

    def save(self, user: User) -> None:
        self._session.execute(
            "INSERT INTO users (username, email, role) VALUES (:u, :e, :r)",
            {"u": user.username, "e": user.email, "r": user.role}
        )


class EmailNotificationService(NotificationService):
    def __init__(self, smtp_client):
        self._client = smtp_client

    def notify_welcome(self, user: User) -> None:
        self._client.send(to=user.email, subject="Welcome", content=f"Welcome {user.username}!")
```

---

## Phần 3: Quy Trình 5 Bước Thực Hiện Refactor Chuẩn Hóa (The 5-Step Refactoring Protocol)

Để đảm bảo tỷ lệ thành công 100% không gây thoái lui (zero regression), AI Coding Agent phải tuân thủ nghiêm ngặt chu trình 5 bước xác định (deterministic protocol) dưới đây. Tuyệt đối không nhảy cóc hay gộp bước.

```
┌───────────────────────────────┐
│ 1. Baseline Verification      │ ◄── Xác lập trạng thái xanh tuyệt đối
└──────────────┬────────────────┘
               ▼
┌───────────────────────────────┐
│ 2. Scope Isolation            │ ◄── Khoanh vùng mổ vi mô & lập kế hoạch
└──────────────┬────────────────┘
               ▼
┌───────────────────────────────┐
│ 3. Micro-Step Refactoring     │ ◄── Sửa từng bước cực nhỏ (Parallel Add -> Switch -> Drop)
└──────────────┬────────────────┘
               ▼
┌───────────────────────────────┐
│ 4. Continuous Test Integrity  │ ◄── Chạy test + typecheck ngay sau mỗi micro-step
└──────────────┬────────────────┘
               ▼
┌───────────────────────────────┐
│ 5. Diff Review & Cleanup      │ ◄── Soi từng dòng diff, dọn rác cá nhân, xác nhận tiêu chí
└───────────────────────────────┘
```

---

### Bước 1: Baseline Verification (Xác lập mốc kiểm chứng ban đầu)
Trước khi chỉnh sửa bất kỳ ký tự nào trong mã nguồn:
1. **Chạy toàn bộ test suite liên quan:** Xác nhận trạng thái hiện tại là **GREEN**.
   * *Nếu test hiện tại đang FAILED:* **DỪNG LẠI NGAY LẬP TỨC**. Thông báo cho người dùng hoặc chuyển sang chế độ debugging sửa lỗi trước, KHÔNG refactor trên một nền tảng đang vỡ.
2. **Đánh giá lưới an toàn (Safety Net):**
   * Kiểm tra xem các hàm/class mục tiêu đã có test bao phủ (coverage) các trường hợp biên (edge cases), exception paths hay chưa.
   * *Nếu chưa có test hoặc test quá sơ sài:* Agent phải viết **Characterization Tests (Tests ghi nhận hành vi hiện tại)** để khóa chặt contract trước khi sửa code sản xuất.

---

### Bước 2: Scope Isolation (Khoanh vùng phẫu thuật & Lập kế hoạch)
1. **Khóa chặt phạm vi (Bounding Box):**
   * Định danh rõ: Danh sách file, class, method cụ thể sẽ can thiệp.
   * Ranh giới bất biến (Invariants): Xác định rõ public interface nào không được phép thay đổi chữ ký (signature) để tránh làm vỡ các caller bên ngoài.
2. **Thiết lập chuỗi thao tác vi mô (Micro-step Plan):**
   * Phân tách bài toán thành danh sách 3 - 5 bước nhỏ độc lập có thể kiểm tra được.
   * *Ví dụ:*
     * *Bước 2.1: Tạo Value Object mới kèm unit tests cho chính nó.*
     * *Bước 2.2: Thay thế tham số bên trong private method.*
     * *Bước 2.3: Chuyển đổi public method qua Adapter hoặc chuyển hướng gọi.*

---

### Bước 3: Micro-Step Refactoring (Biến đổi từng bước vi mô)
Áp dụng chiến lược **Parallel Change (Expand and Contract)** của Martin Fowler:
1. **Expand (Tạo mới song song):** Tạo hàm mới, class mới hoặc cấu trúc mới sạch sẽ bên cạnh code cũ. Code cũ vẫn hoạt động nguyên vẹn.
2. **Switch (Chuyển hướng an toàn):** Từng bước chuyển các điểm gọi nội bộ từ code cũ sang code mới.
3. **Contract (Thu hồi code cũ):** Sau khi toàn bộ các điểm gọi đã trỏ vào cấu trúc mới an toàn, tiến hành dỡ bỏ code cũ.

> **Quy tắc vàng:** Không bao giờ xóa code cũ và viết lại code mới cùng một lúc trong một thao tác sửa file lớn (large monolithic replacement). Điều này khiến agent mất kiểm soát ngữ nghĩa và rất khó debug khi test fail.

---

### Bước 4: Continuous Test & Integrity Verification (Chạy test liên tục)
Ngay sau **mỗi một micro-step**:
1. **Chạy ngay bộ test:** Xác nhận không có bài test nào bị vỡ.
2. **Chạy Static Type Checker:** (`mypy`, `tsc`) để đảm bảo không sai lệch kiểu dữ liệu hoặc tham số `None/null`.
3. **Chạy Linter:** (`ruff`, `eslint`) trên đúng file vừa chỉnh sửa để đảm bảo không vi phạm convention của dự án.
4. *Quy tắc rollback:* Nếu một micro-step làm vỡ test mà không giải quyết được trong vòng 1 lần thử, agent phải **revert ngay lập tức** về micro-step xanh gần nhất trước đó thay vì viết chồng chéo các bản vá tạm bợ.

---

### Bước 5: Diff Review & Mess Cleanup (Soi diff phẫu thuật & Dọn sạch rác cá nhân)
Trước khi kết luận hoàn thành:
1. **Tự soi từng dòng `git diff`:**
   * Tự hỏi: *"Dòng này có phục vụ trực tiếp cho mục tiêu refactor không?"*
   * Loại bỏ các dòng whitespace thừa, căn lề vô nghĩa, format đổi dòng ngẫu nhiên.
2. **Dọn sạch rác cá nhân (Clean up your own mess):**
   * Xóa toàn bộ unused imports sinh ra do quá trình di dời code.
   * Xóa các biến tạm thời, hàm helper thử nghiệm không còn dùng.
3. **Tôn trọng rác của người khác:** Nếu thấy code thừa, dead code có sẵn từ trước nằm ngoài phạm vi refactor, **hãy để nguyên** và ghi chú vào báo cáo, không tự ý xóa.

---

## Phần 4: Các Anti-Patterns AI Agent Hay Mắc Phải Khi Refactor (AI Refactoring Pitfalls)

| Anti-Pattern | Biểu Hiện Đặc Trưng | Hậu Quả Kỹ Thuật | Kỷ Luật Khắc Phục Bắt Buộc |
|:---|:---|:---|:---|
| **1. The Wandering Refactorer** *(Sửa lan man ngoài phạm vi)* | Được giao refactor hàm A, agent thấy hàm B ở file khác chưa chuẩn liền "tiện tay" sửa luôn cả module B và C. | Tạo ra diff khổng lồ, xung đột merge nghiêm trọng, khó review và dễ gây regression ngoài dự kiến. | **Kỷ luật ranh giới:** Tuyệt đối không sửa file/symbol nằm ngoài danh sách đã xác định ở Bước 2. |
| **2. Cosmetic Drift & Style War** *(Đổi style không cần thiết)* | Tự ý đổi nháy đơn sang nháy kép, đổi thứ tự imports, căn lại thụt lề cả file, viết lại docstring theo phong cách cá nhân. | Gây nhiễu loạn lịch sử `git blame`, diff hàng ngàn dòng che lấp logic thực sự bị thay đổi. | **Kỷ luật bảo tồn:** Giữ nguyên style hiện có của tệp. Chỉ format khi có linter rule bắt buộc của repo. |
| **3. Over-Abstraction (YAGNI Violation)** *(Trừu tượng hóa quá đà)* | Tạo hàng loạt Factory, Dynamic Dispatchers, 3 tầng abstract classes chỉ để giải quyết một hàm if/else có 2 điều kiện cố định. | Tăng tải nhận thức (cognitive load), làm chậm tốc độ đọc hiểu mã nguồn của con người. | **Quy tắc số 3 (Rule of Three):** Chỉ tạo abstraction khi logic lặp lại từ 3 lần trở lên hoặc cần thiết lập test boundary. |
| **4. Silent Semantic Drift** *(Lệch ngữ nghĩa ngầm)* | Đảo điều kiện `if` sang Guard Clause nhưng quên mất tác dụng ngắn mạch (short-circuit), làm thay đổi thứ tự gọi side-effects, hoặc đổi kiểu trả về từ `None` sang `[]`. | Gây lỗi tiềm ẩn nghiêm trọng trên production mà unit test thông thường có thể bỏ lọt. | **Phân tích Truth Table:** Lập bảng chân trị và kiểm tra thứ tự thực thi của side-effects trước khi đảo điều kiện. |
| **5. Premature Dead Code Deletion** *(Tự ý xóa code của người khác)* | Thấy một method không được gọi trực tiếp trong file liền kết luận là "dead code" và xóa bỏ. | Phá vỡ dynamic invocation, reflection, serialization, hoặc làm vỡ consumer từ các repo/service khác. | **Không xóa code người khác:** Chỉ xóa code do chính mình vừa trích xuất xong. Với code nghi vấn, ghi nhận vào phần note. |

---

## Phần 5: Verification Checklist Trước Khi Tuyên Bố Hoàn Thành (Pre-Completion Checklist)

Mỗi AI Coding Agent phải tự thực hiện duyệt qua từng tiêu chí dưới đây. Chỉ khi **100% các tiêu chí bắt buộc đạt yêu cầu (PASS)**, agent mới được phép tuyên bố hoàn thành nhiệm vụ.

### Nhóm A: Bảo Toàn Hợp Đồng & Ngữ Nghĩa (Contract & Behavior Preservation)
- [ ] **A.1 (Bắt buộc):** Tất cả các public functions/methods vẫn giữ nguyên chữ ký (name, parameter names, types, default values) hoặc đã được trang bị backward-compatible adapters.
- [ ] **A.2 (Bắt buộc):** Không thay đổi kiểu trả về (return type contracts) và các ngoại lệ cụ thể (exceptions) được ném ra trong các ca biên (edge cases).
- [ ] **A.3 (Bắt buộc):** Thứ tự thực thi của các side-effects (Database writes, Network I/O, Logging, Event emits) được bảo toàn nguyên vẹn.
- [ ] **A.4 (Bắt buộc):** Short-circuit evaluation (`and`/`or`, `&&`/`||`) trong các biểu thức điều kiện không bị đảo lộn dẫn đến chạy ngoài ý muốn.

### Nhóm B: Kỷ Luật Phẫu Thuật & Phạm Vi (Surgical Discipline & Scope)
- [ ] **B.1 (Bắt buộc):** Toàn bộ diff chỉ tập trung vào các files/modules đã được định nghĩa trong phạm vi nhiệm vụ.
- [ ] **B.2 (Bắt buộc):** Không có thay đổi định dạng (formatting/whitespace) ở các dòng code không liên quan.
- [ ] **B.3 (Bắt buộc):** Không tự ý xóa dead code của người khác (chỉ dọn sạch rác và helpers phát sinh do chính mình tạo ra).
- [ ] **B.4 (Bắt buộc):** Các imports không còn sử dụng (unused imports) đã được loại bỏ sạch sẽ.

### Nhóm C: Kiến Trúc & Clean Code (Architecture & SOLID Standards)
- [ ] **C.1 (Bắt buộc):** Tuân thủ Dependency Rule: Tầng Domain/Entities không import ngược tầng Framework, Database hoặc UI.
- [ ] **C.2 (Bắt buộc):** Single Responsibility: Mỗi function/method sau refactor tập trung làm một việc ở một tầng trừu tượng nhất định.
- [ ] **C.3 (Bắt buộc):** Guard Clauses đã loại bỏ triệt để các khối `if/else` lồng nhau sâu quá 2 cấp độ.
- [ ] **C.4 (Bắt buộc):** Không có code abstraction suy đoán (No speculative generality) vi phạm nguyên tắc YAGNI.

### Nhóm D: Kiểm Định Tự Động (Automated Quality Gates)
- [ ] **D.1 (Bắt buộc):** Toàn bộ unit tests và regression tests liên quan đều đạt trạng thái **GREEN (PASS)**.
- [ ] **D.2 (Bắt buộc):** Type checker (`mypy`, `tsc`, v.v.) kiểm tra thành công, không phát sinh cảnh báo lỗi kiểu mới nào.
- [ ] **D.3 (Bắt buộc):** Linter (`ruff`, `eslint`, v.v.) không báo bất kỳ vi phạm quy chuẩn nào trên các file đã sửa.
- [ ] **D.4 (Bắt buộc):** Không có bất kỳ hardcoded secrets, print debug tạm thời hoặc credentials nào sót lại trong code.

---

## Tóm Tắt Bản Tuyên Ngôn Của AI Refactoring Agent
1. **Codebase là của con người, Agent là người phục vụ:** Tôn trọng văn phong và cấu trúc sẵn có.
2. **Không có kiểm chứng, không có refactor:** Nếu không thể test, hãy viết test trước khi sửa.
3. **Mỗi vết cắt phải chuẩn xác như phẫu thuật:** Thay đổi nhỏ nhất có thể để đạt được độ trong sáng lớn nhất.
