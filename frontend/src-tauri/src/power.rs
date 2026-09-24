//! OS 자동 절전 방지 — **전용 스레드 하나**가 소유한다.
//!
//! **왜 스레드를 따로 두나.** Windows 의 `SetThreadExecutionState` 는 *호출한 스레드의* 실행
//! 요구를 바꾼다(Microsoft Learn `winbase.h`, 확인 2026-09-22). 걸고 푸는 것이 같은 스레드가
//! 아니면 **푼 줄 알았는데 안 풀리거나, 그 스레드가 끝나며 몰래 풀린다.** Tauri 커맨드는
//! async 런타임의 아무 워커에서나 돌 수 있으므로, **OS 호출만 이 스레드로 모은다.**
//! macOS 의 IOPMAssertion 은 스레드에 매이지 않지만 같은 구조로 둔다 — 한 자리에서만
//! OS 를 만지는 편이 경합을 없애고, 두 OS 의 코드가 갈라지지 않는다.
//!
//! **여기에 타이머가 없다**(WORK I-3). 이 스레드는 스스로 아무것도 풀지 않는다 —
//! 오직 받은 `Engage`·`Disengage` 만 수행한다.

use std::sync::mpsc::{channel, Sender};
use std::thread::JoinHandle;

/// OS 호출의 결과. SPEC-006 §4 의 `state` 로 옮겨지는 값이다.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PowerOutcome {
    /// 자동 절전 방지가 **걸려 있다**.
    Engaged,
    /// 풀렸다(남은 세션 0).
    Released,
    /// 이 플랫폼에 구현이 없다 — `E-02`.
    /// **macOS·Windows 빌드에서는 만들어지지 않는다**(둘 다 구현이 있다). 지원 OS 밖에서만 나온다.
    #[allow(dead_code)]
    Unsupported,
    /// OS 호출이 실패했다 — `E-03`.
    Failed(String),
}

/// OS 를 실제로 만지는 자리. 테스트는 여기에 가짜를 꽂는다.
pub trait PowerBackend: Send + 'static {
    /// **이미 걸려 있으면 그대로 두고, 풀려 있으면 다시 건다**(재무장 — `E-09`).
    fn engage(&mut self, reason: &str) -> PowerOutcome;
    fn disengage(&mut self) -> PowerOutcome;
}

enum Op {
    Engage(String),
    Disengage,
    /// **명시적 종료.** 채널이 닫히기를 기다리지 않는다 — `Sender` 사본이 하나라도 살아 있으면
    /// `recv()` 가 영원히 끝나지 않고, `Drop` 의 `join()` 이 그대로 매달린다(실제로 겪었다).
    /// 종료를 «사건»으로 보내면 바깥에 사본이 몇 개 남았든 스레드가 스스로 끝난다.
    Shutdown,
}

struct Job {
    op: Op,
    reply: Sender<PowerOutcome>,
}

/// 전용 스레드로 가는 손잡이.
pub struct PowerThread {
    tx: Sender<Job>,
    handle: Option<JoinHandle<()>>,
}

impl PowerThread {
    /// 이 플랫폼의 기본 구현으로 스레드를 띄운다.
    pub fn spawn() -> Self {
        Self::spawn_with(platform_backend())
    }

    pub fn spawn_with<B: PowerBackend>(mut backend: B) -> Self {
        let (tx, rx) = channel::<Job>();
        let handle = std::thread::Builder::new()
            .name("scax-wake-guard".into())
            .spawn(move || {
                // 채널이 닫히면(= 셸이 내려간다) 루프가 끝난다. 그때 **반드시 풀고 나간다** —
                // 같은 스레드에서 풀어야 Windows 의 실행 상태가 실제로 돌아온다.
                while let Ok(job) = rx.recv() {
                    let outcome = match job.op {
                        Op::Engage(reason) => backend.engage(&reason),
                        Op::Disengage => backend.disengage(),
                        Op::Shutdown => break,
                    };
                    // 부른 쪽이 이미 사라졌어도 OS 조작은 끝난 뒤다 — 결과만 버린다.
                    let _ = job.reply.send(outcome);
                }
                // 어느 길로 나가든(종료 사건 · 채널 닫힘) **반드시 풀고 나간다.**
                // 같은 스레드에서 풀어야 Windows 의 실행 상태가 실제로 돌아온다.
                backend.disengage();
            })
            .expect("절전 방지 스레드를 띄우지 못했습니다");
        Self {
            tx,
            handle: Some(handle),
        }
    }

    pub fn engage(&self, reason: &str) -> PowerOutcome {
        self.send(Op::Engage(reason.to_string()))
    }

    pub fn disengage(&self) -> PowerOutcome {
        self.send(Op::Disengage)
    }

    fn send(&self, op: Op) -> PowerOutcome {
        let (reply_tx, reply_rx) = channel();
        if self
            .tx
            .send(Job {
                op,
                reply: reply_tx,
            })
            .is_err()
        {
            return PowerOutcome::Failed("절전 방지 스레드가 없습니다.".into());
        }
        // 이 스레드는 OS 호출만 한다 — 다시 이쪽을 부르지 않으므로 교착이 생기지 않는다.
        reply_rx
            .recv()
            .unwrap_or_else(|_| PowerOutcome::Failed("절전 방지 스레드가 응답하지 않았습니다.".into()))
    }
}

impl Drop for PowerThread {
    fn drop(&mut self) {
        // **종료를 «보낸» 뒤** 스레드가 스스로 풀고 나가는 것을 기다린다.
        // 보내기가 실패하면 스레드가 이미 끝난 것이므로 join 이 곧바로 돌아온다.
        let (reply_tx, _reply_rx) = channel();
        let _ = self.tx.send(Job {
            op: Op::Shutdown,
            reply: reply_tx,
        });
        if let Some(handle) = self.handle.take() {
            let _ = handle.join();
        }
    }
}

fn platform_backend() -> Box<dyn PowerBackend> {
    #[cfg(target_os = "macos")]
    {
        Box::new(macos::Assertion::default())
    }
    #[cfg(windows)]
    {
        Box::new(windows::ExecutionState::default())
    }
    #[cfg(not(any(target_os = "macos", windows)))]
    {
        Box::new(Unsupported)
    }
}

impl PowerBackend for Box<dyn PowerBackend> {
    fn engage(&mut self, reason: &str) -> PowerOutcome {
        (**self).engage(reason)
    }
    fn disengage(&mut self) -> PowerOutcome {
        (**self).disengage()
    }
}

/// 지원 OS 는 macOS·Windows 둘이다(SPEC-006 §4 Data Contract). 그 밖에서는 `E-02` 다.
#[cfg(not(any(target_os = "macos", windows)))]
struct Unsupported;

#[cfg(not(any(target_os = "macos", windows)))]
impl PowerBackend for Unsupported {
    fn engage(&mut self, _reason: &str) -> PowerOutcome {
        PowerOutcome::Unsupported
    }
    fn disengage(&mut self) -> PowerOutcome {
        PowerOutcome::Released
    }
}

#[cfg(target_os = "macos")]
mod macos {
    use super::{PowerBackend, PowerOutcome};
    use std::ffi::c_void;

    // `IOKit/pwr_mgt/IOPMLib.h` — 고르는 것은 `PreventUserIdleSystemSleep` 하나다.
    // "Prevents the system from sleeping automatically due to a lack of user activity."
    // `…NoDisplaySleep` 계열은 **화면까지 켜 둬** DEC-005 D-03 의 「화면 꺼짐 허용」을 어기고,
    // `…PreventSystemSleep`(Dark Wake)은 이번 요구를 넘는다.
    const ASSERTION_TYPE: &str = "PreventUserIdleSystemSleep";
    const ASSERTION_LEVEL_ON: u32 = 255;
    const KERN_SUCCESS: i32 = 0;
    const CF_STRING_ENCODING_UTF8: u32 = 0x0800_0100;

    #[link(name = "CoreFoundation", kind = "framework")]
    extern "C" {
        fn CFStringCreateWithBytes(
            alloc: *const c_void,
            bytes: *const u8,
            num_bytes: isize,
            encoding: u32,
            is_external_representation: u8,
        ) -> *const c_void;
        fn CFRelease(cf: *const c_void);
    }

    #[link(name = "IOKit", kind = "framework")]
    extern "C" {
        fn IOPMAssertionCreateWithName(
            assertion_type: *const c_void,
            assertion_level: u32,
            assertion_name: *const c_void,
            assertion_id: *mut u32,
        ) -> i32;
        fn IOPMAssertionRelease(assertion_id: u32) -> i32;
    }

    struct CfString(*const c_void);

    impl CfString {
        fn new(value: &str) -> Option<Self> {
            let raw = unsafe {
                CFStringCreateWithBytes(
                    std::ptr::null(),
                    value.as_ptr(),
                    value.len() as isize,
                    CF_STRING_ENCODING_UTF8,
                    0,
                )
            };
            if raw.is_null() {
                None
            } else {
                Some(Self(raw))
            }
        }
    }

    impl Drop for CfString {
        fn drop(&mut self) {
            unsafe { CFRelease(self.0) };
        }
    }

    #[derive(Default)]
    pub struct Assertion {
        pub(super) id: Option<u32>,
    }

    impl Assertion {
        /// assertion 하나를 새로 만든다. 성공하면 그 id.
        fn create(reason: &str) -> Result<u32, String> {
            let Some(kind) = CfString::new(ASSERTION_TYPE) else {
                return Err("CFString 생성 실패(type)".into());
            };
            let Some(name) = CfString::new(reason) else {
                return Err("CFString 생성 실패(name)".into());
            };
            let mut id: u32 = 0;
            let result =
                unsafe { IOPMAssertionCreateWithName(kind.0, ASSERTION_LEVEL_ON, name.0, &mut id) };
            if result == KERN_SUCCESS {
                Ok(id)
            } else {
                Err(format!("IOPMAssertionCreateWithName 실패: {result}"))
            }
        }
    }

    impl PowerBackend for Assertion {
        /// **재무장을 실제로 한다 — 「id 를 들고 있으니 됐다」로 끝내지 않는다(리뷰 W-4).**
        ///
        /// 옛 구조는 `if self.id.is_some() { return Engaged }` 였다. 그러면 OS 가 그 사이
        /// assertion 을 거둬갔어도 **알 길이 없고**, `E-09` 가 요구하는 「그 시점에 걸려 있지
        /// 않으면 다시 건다」의 절반이 macOS 에서 형식만 남는다(L-14 의 복귀 재확인이 무의미해진다).
        /// macOS 에는 「이 id 가 아직 살아 있나」를 싸게 되묻는 수단이 없으므로,
        /// **새로 만든 뒤 옛것을 놓는 방식(make-before-break)** 으로 «지금 걸려 있음»을 보장한다.
        /// 순서를 뒤집지 않으므로 **재무장 도중에도 보호가 끊기지 않는다.**
        fn engage(&mut self, reason: &str) -> PowerOutcome {
            let previous = self.id.take();
            match Self::create(reason) {
                Ok(fresh) => {
                    self.id = Some(fresh);
                    // 새것이 선 뒤에 옛것을 놓는다. 실패해도 새것이 서 있으므로 보호는 유지된다.
                    if let Some(stale) = previous {
                        let released = unsafe { IOPMAssertionRelease(stale) };
                        if released != KERN_SUCCESS {
                            // 놓지 못한 옛 assertion 은 프로세스가 끝날 때 OS 가 회수한다(L-13).
                            // 여기서 새 id 를 버리면 «지금» 보호가 사라지므로 버리지 않는다.
                        }
                    }
                    PowerOutcome::Engaged
                }
                Err(reason) => {
                    // 새로 만들지 못했다. **옛 id 를 도로 들고 있는다** — 여기서 버리면
                    // 「실패했는데 정리할 손잡이까지 잃는」 최악이 된다(해제 때 이 id 로 푼다).
                    self.id = previous;
                    // 상태는 낙관하지 않는다 — 방금 OS 호출이 실패했으므로 `degraded` 로 답한다.
                    PowerOutcome::Failed(reason)
                }
            }
        }

        fn disengage(&mut self) -> PowerOutcome {
            let Some(id) = self.id.take() else {
                return PowerOutcome::Released;
            };
            let result = unsafe { IOPMAssertionRelease(id) };
            if result == KERN_SUCCESS {
                PowerOutcome::Released
            } else {
                PowerOutcome::Failed(format!("IOPMAssertionRelease 실패: {result}"))
            }
        }
    }
}

#[cfg(windows)]
mod windows {
    use super::{PowerBackend, PowerOutcome};

    // Microsoft Learn `SetThreadExecutionState` (확인 2026-09-22).
    // `ES_DISPLAY_REQUIRED` 를 넣으면 화면이 안 꺼져 D-03 을 어기고,
    // `ES_AWAYMODE_REQUIRED` 는 「잠든 것처럼 보이면서 도는」 모드라 이번 요구가 아니다.
    const ES_CONTINUOUS: u32 = 0x8000_0000;
    const ES_SYSTEM_REQUIRED: u32 = 0x0000_0001;

    #[link(name = "kernel32")]
    extern "system" {
        fn SetThreadExecutionState(es_flags: u32) -> u32;
    }

    #[derive(Default)]
    pub struct ExecutionState {
        /// **「요구를 건 적이 있다」**는 사실. 보고용이고, 해제 여부를 가르는 데 쓰지 않는다.
        requested: bool,
    }

    impl PowerBackend for ExecutionState {
        fn engage(&mut self, _reason: &str) -> PowerOutcome {
            // `ES_CONTINUOUS` 와 함께 부르면 **다음 호출까지 유지**된다 — 주기적 갱신이 필요 없다.
            // 다시 불러도 늘지 않으므로 재무장(`E-09`)은 같은 호출 한 번이다.
            let previous = unsafe { SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) };
            // **실패해도 이 표시를 내리지 않는다(리뷰 W-5).** 앞선 engage 로 OS 에 요구가 서 있는데
            // 뒤따른 재무장이 0 을 돌려줬다고 표시를 내리면, 해제 때 「걸린 적 없다」로 읽혀
            // **요구가 스레드 수명 내내 남는다.**
            self.requested = true;
            if previous == 0 {
                PowerOutcome::Failed("SetThreadExecutionState 실패".into())
            } else {
                PowerOutcome::Engaged
            }
        }

        fn disengage(&mut self) -> PowerOutcome {
            // **이른 반환을 두지 않는다(리뷰 W-5).** engage 가 실패로 «보였어도» OS 에는
            // 요구가 서 있을 수 있다. `ES_CONTINUOUS` 단독 호출은 아무것도 서 있지 않을 때도
            // 무해하므로, 상태를 믿지 말고 **언제나 지운다.**
            let previous = unsafe { SetThreadExecutionState(ES_CONTINUOUS) };
            let had_requested = self.requested;
            self.requested = false;
            if previous == 0 && had_requested {
                PowerOutcome::Failed("SetThreadExecutionState(해제) 실패".into())
            } else {
                PowerOutcome::Released
            }
        }
    }
}

/// 실기에서만 도는 검증 — **가짜 OS 를 끼우지 않고 진짜 API 를 부른다.**
#[cfg(target_os = "macos")]
#[cfg(test)]
mod macos_tests {
    use super::macos::Assertion;
    use super::{PowerBackend, PowerOutcome};

    #[test]
    fn 재무장이_assertion_을_실제로_다시_만든다() {
        // **리뷰 W-4 의 회귀 시험.** 옛 구조는 `if self.id.is_some() { return Engaged }` 라
        // 두 번째 engage 가 OS 를 만지지 않았다 — id 가 그대로면 그 시절로 돌아간 것이다.
        let mut assertion = Assertion::default();
        assert_eq!(assertion.engage("회의 녹음 중"), PowerOutcome::Engaged);
        let first = assertion.id.expect("첫 assertion 이 서야 한다");

        assert_eq!(assertion.engage("회의 녹음 중"), PowerOutcome::Engaged);
        let second = assertion.id.expect("재무장 뒤에도 assertion 이 서 있어야 한다");

        assert_ne!(
            first, second,
            "재무장이 새 assertion 을 만들지 않았다 — W-4 의 no-op 으로 되돌아갔다"
        );

        // 정리까지 확인한다 — 시험이 OS 에 점유를 남기지 않는다.
        assert_eq!(assertion.disengage(), PowerOutcome::Released);
        assert!(assertion.id.is_none());
    }

    #[test]
    fn 해제는_멱등이고_들고_있지_않으면_곧바로_released() {
        let mut assertion = Assertion::default();
        assert_eq!(assertion.disengage(), PowerOutcome::Released);
        assertion.engage("회의 녹음 중");
        assert_eq!(assertion.disengage(), PowerOutcome::Released);
        assert_eq!(assertion.disengage(), PowerOutcome::Released);
    }
}

/// ⚠ **이 기기에서 실행되지 않았다.** Windows 실기가 없고, 이 호스트에는 `llvm-rc` 가 없어
/// `cargo check --target x86_64-pc-windows-msvc` 조차 빌드 스크립트 단계에서 멈춘다.
/// 실기를 얻으면 가장 먼저 돌릴 것.
#[cfg(windows)]
#[cfg(test)]
mod windows_tests {
    use super::windows::ExecutionState;
    use super::{PowerBackend, PowerOutcome};

    #[test]
    fn 해제는_engage_성패와_무관하게_요구를_지운다() {
        // **리뷰 W-5 의 회귀 시험.** 옛 구조는 `if !self.engaged { return Released }` 로
        // 곧장 빠져나가, engage 가 «실패로 보인» 뒤에는 OS 요구가 스레드 수명 내내 남았다.
        let mut state = ExecutionState::default();
        // 한 번도 걸지 않은 상태에서도 해제가 통과해야 한다(이른 반환이 없다).
        assert_eq!(state.disengage(), PowerOutcome::Released);

        assert_eq!(state.engage("회의 녹음 중"), PowerOutcome::Engaged);
        assert_eq!(state.disengage(), PowerOutcome::Released);
        // 두 번 풀어도 안전하다.
        assert_eq!(state.disengage(), PowerOutcome::Released);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Mutex};
    use std::thread::ThreadId;

    #[derive(Debug, Default)]
    struct Log {
        calls: Vec<(&'static str, ThreadId)>,
        engaged: bool,
        dropped_engaged: Option<bool>,
    }

    struct Spy(Arc<Mutex<Log>>);

    impl PowerBackend for Spy {
        fn engage(&mut self, _reason: &str) -> PowerOutcome {
            let mut log = self.0.lock().unwrap();
            log.calls.push(("engage", std::thread::current().id()));
            log.engaged = true;
            PowerOutcome::Engaged
        }
        fn disengage(&mut self) -> PowerOutcome {
            let mut log = self.0.lock().unwrap();
            log.calls.push(("disengage", std::thread::current().id()));
            log.engaged = false;
            PowerOutcome::Released
        }
    }

    impl Drop for Spy {
        fn drop(&mut self) {
            let mut log = self.0.lock().unwrap();
            log.dropped_engaged = Some(log.engaged);
        }
    }

    #[test]
    fn os_호출은_언제나_같은_스레드에서_돈다() {
        // Windows `SetThreadExecutionState` 의 thread-bound 제약이 이 검증의 이유다.
        // 여러 호출 스레드에서 걸고 풀어도 OS 를 만지는 스레드는 **하나여야 한다**.
        let log = Arc::new(Mutex::new(Log::default()));
        let power = Arc::new(PowerThread::spawn_with(Spy(log.clone())));
        let callers: Vec<_> = (0..8)
            .map(|index| {
                let power = power.clone();
                std::thread::spawn(move || {
                    if index % 2 == 0 {
                        power.engage("회의 녹음 중")
                    } else {
                        power.disengage()
                    }
                })
            })
            .collect();
        for caller in callers {
            assert!(matches!(
                caller.join().unwrap(),
                PowerOutcome::Engaged | PowerOutcome::Released
            ));
        }
        let log = log.lock().unwrap();
        assert_eq!(log.calls.len(), 8);
        let first = log.calls[0].1;
        assert!(
            log.calls.iter().all(|(_, id)| *id == first),
            "OS 호출이 여러 스레드로 흩어졌다: {:?}",
            log.calls
        );
        assert_ne!(first, std::thread::current().id(), "호출 스레드에서 직접 OS 를 만졌다");
    }

    #[test]
    fn 스레드가_내려갈_때_반드시_푼다() {
        // 셸이 끝나는 길(L-13 의 앞자리)에서 실행 상태가 남지 않아야 한다.
        let log = Arc::new(Mutex::new(Log::default()));
        {
            let power = PowerThread::spawn_with(Spy(log.clone()));
            assert_eq!(power.engage("회의 녹음 중"), PowerOutcome::Engaged);
        }
        let log = log.lock().unwrap();
        assert_eq!(log.dropped_engaged, Some(false), "내려가면서 풀지 않았다");
        assert_eq!(log.calls.last().map(|c| c.0), Some("disengage"));
    }

    #[test]
    fn 바깥에_sender_사본이_남아_있어도_종료가_끝난다() {
        // 회귀 시험. 예전 `Drop` 은 「원래 sender 를 버리면 채널이 닫힌다」에 기대 `join()` 했는데,
        // 사본이 하나라도 살아 있으면 `recv()` 가 끝나지 않아 **영원히 매달렸다**.
        // 종료가 «사건»이 된 지금은 사본이 남아 있어도 끝난다.
        let log = Arc::new(Mutex::new(Log::default()));
        let power = PowerThread::spawn_with(Spy(log.clone()));
        let survivor = power.tx.clone();
        assert_eq!(power.engage("회의 녹음 중"), PowerOutcome::Engaged);

        let (done_tx, done_rx) = channel();
        std::thread::spawn(move || {
            drop(power);
            let _ = done_tx.send(());
        });
        // 시험 쪽 시간 제한이다 — 제품에 타이머를 들이는 것이 아니라,
        // 매달리는 실패를 **끝나지 않는 시험** 대신 붉은 시험으로 만든다(I-3 과 무관).
        done_rx
            .recv_timeout(std::time::Duration::from_secs(10))
            .expect("종료가 끝나지 않았다 — Drop 이 다시 매달린다");

        drop(survivor);
        let log = log.lock().unwrap();
        assert_eq!(log.dropped_engaged, Some(false), "내려가면서 풀지 않았다");
    }

    #[test]
    fn 스레드가_없으면_매달리지_않고_실패로_답한다() {
        // `E-14a` 의 네이티브 쪽 — 커맨드가 영원히 매달리면 녹음 화면이 멎는다.
        let (tx, rx) = channel::<Job>();
        drop(rx);
        let power = PowerThread { tx, handle: None };
        assert!(matches!(power.engage("회의 녹음 중"), PowerOutcome::Failed(_)));
        assert!(matches!(power.disengage(), PowerOutcome::Failed(_)));
    }
}
