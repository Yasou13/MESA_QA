# MESA-QA — BAĞIMSIZ UYGULAMA DOĞRULAMA VE REGRESYON AUDIT SÖZLEŞMESİ

## 1. Belgenin Amacı

Bu belge, implement edilmiş `MESA-QA` repository'sinin:

* orijinal `MESA_QA_AUTONOMOUS_TEST_ENGINEER_SPEC.md` spesifikasyonuna,
* gerçek güncel MESA repository'sine,
* güvenlik ve izolasyon kurallarına,
* zero-extra-cost şartına,
* Codex Tester / Repairer ayrımına,
* gerçek MCP kullanımına,
* autonomous repair modeline,
* düşük kaynak tüketimi hedefine

gerçekten uyup uymadığını bağımsız olarak denetlemek için hazırlanmıştır.

Bu audit:

> “Kod var mı?”

sorusunu değil:

> “Sistem gerçekten tasarlandığı gibi çalışıyor mu, güvenli mi ve iddia ettiği entegrasyonlar gerçek mi?”

sorusunu cevaplamalıdır.

---

# 2. Denetimde Kullanılacak Kaynaklar

Auditor üç kaynağı birlikte incelemelidir:

```text
1. MESA_QA_AUTONOMOUS_TEST_ENGINEER_SPEC.md

2. Implement edilmiş MESA-QA repository

3. Güncel gerçek MESA repository
```

Öncelik:

```text
Gerçek runtime davranışı
>
Gerçek kod
>
Test kanıtı
>
Dokümantasyon
>
Agent iddiası
```

README veya önceki agent'ın final mesajı kanıt değildir.

---

# 3. Ana Denetim Sorusu

MESA-QA gerçekten şu sistemi oluşturuyor mu?

```text
               MESA-QA
                  │
          Codex Test Engineer
                  │
               MESA MCP
                  │
                  ▼
          QA Candidate MESA
                  │
              QA Storage
                  │
                  ▼
          observed behavior
                  │
                  ▼
          Ground Truth Store
                  │
          expected vs actual
                  │
               anomaly
                  │
              reproduce
                  │
           confirmed bug
                  │
                  ▼
           Repair Codex
                  │
                  ▼
        Candidate Worktree
                  │
            regression
                  │
                fix
                  │
               commit
                  │
              restart
                  │
          live verification
                  │
                  └──────→ test continues
```

Bunun herhangi bir kritik halkası fake, mocked-only, bypass edilmiş veya implement edilmemişse audit bunu açıkça göstermelidir.

---

# 4. Kritik Invariantlar

Aşağıdaki invariantlardan herhangi biri FAIL ise sonuç otomatik:

```text
NO-GO
```

olmalıdır.

## INV-01 — MESA Main Değişmezliği

Autonomous test ve repair sırasında:

```text
original MESA checkout
```

değiştirilememelidir.

Kontrol:

```text
HEAD SHA before
HEAD SHA after

git status before
git status after

tracked diff before
tracked diff after
```

Original checkout'a autonomous write tespit edilirse:

```text
P0
NO-GO
```

---

# 5. Candidate Worktree İzolasyonu

Repair yalnız:

```text
qa/autonomous-<run_id>
```

branch'li candidate worktree üzerinde gerçekleşmelidir.

Doğrula:

```text
candidate != original checkout

candidate is real git worktree

candidate branch doğru

repair cwd candidate

runtime candidate source'dan çalışıyor
```

Sadece worktree oluşturulmuş olması yeterli değildir.

**Gerçek MESA process'inin candidate kodundan çalıştığı kanıtlanmalıdır.**

---

# 6. Storage İzolasyonu

QA:

```text
gerçek MESA storage
```

kullanmamalıdır.

Doğrula:

```text
configured QA storage root

resolved absolute path

original storage absolute path

paths do not overlap
```

Şu edge-case'leri de dene:

```text
../
symlink
relative path
same directory under alternate spelling
```

Storage overlap mümkünse:

```text
P0
NO-GO
```

---

# 7. MESA-QA Detachable mı?

MESA repository:

```text
MESA-QA import etmemeli
MESA-QA dependency içermemeli
MESA-QA daemon gerektirmemeli
```

MESA-QA tamamen kapatıldığında normal MESA çalışabilmelidir.

Dependency direction:

```text
MESA-QA → MESA
```

olmalıdır.

Tersi tespit edilirse mimari ihlal olarak raporlanmalıdır.

---

# 8. Gerçek MCP Kullanımı

Tester Codex'in normal test yolu:

```text
Tester
↓
MESA MCP
↓
canonical MESA runtime
```

olmalıdır.

Doğrula:

* MCP server gerçekten başlatılıyor mu?
* Codex gerçekten MCP server'a bağlanıyor mu?
* Gerçek tool discovery gerçekleşiyor mu?
* `remember/recall/improve/forget/...` çağrıları gerçek MCP üzerinden mi?
* Mock adapter production flow'da kullanılıyor mu?
* Direct DB veya internal Python import bypass var mı?

Şu kabul edilmez:

```text
Tester → SQLite directly
Tester → Vector DB directly
Tester → internal repository/service imports
```

---

# 9. Tester / Repairer Ayrımı

Tester:

```text
MESA source write = forbidden
```

Repairer:

```text
candidate worktree write = allowed
```

olmalıdır.

Bunun yalnız prompt seviyesinde değil:

```text
filesystem
cwd
sandbox
controller validation
```

seviyesinde zorlandığını doğrula.

Tester'ın MESA source değiştirebildiği durumda:

```text
P0/P1
```

olarak değerlendir.

---

# 10. Ground Truth Store Doğruluğu

Ground Truth Store:

```text
local
deterministic
independent from MESA
```

olmalıdır.

Ground Truth:

```text
current state
history
forgotten state
expected result
```

bilgilerini tutmalıdır.

Aşağıdakiler yanlış tasarımdır:

```text
LLM = tek judge

MESA cevabı = expected truth

Ground Truth MESA storage'dan okunuyor

Ground Truth semantic retrieval implement ediyor
```

Ground Truth, MESA'nın kopyası olmamalıdır.

---

# 11. Scenario Engine

Gerçekten mevcut mu ve deterministik mi?

Minimum alanlar:

```text
basic memory
multi-fact
duplicate
semantic duplicate
correction
multiple correction
current truth
historical truth
forget
session transition
cross-session
restart
paraphrase
temporal
conflict
idempotency
mixed endurance
```

Seed kullanılıyorsa aynı seed ile scenario yeniden oluşturulabilmelidir.

---

# 12. Codex Gerçekten Sistemi Kullanıyor mu?

Bu kritik kontroldür.

Sadece Python script:

```text
remember
recall
remember
recall
```

yapıyor, Codex ise yalnız hata olduğunda çağrılıyorsa:

> sistem spesifikasyonun “Codex gerçek AI kullanıcı gibi MESA'yı kullansın” hedefini kısmen karşılar.

Audit bunu:

```text
PARTIAL
```

olarak işaretlemelidir.

Codex Tester'ın gerçekten:

```text
doğal sorgular
paraphrase
exploratory behavior
session rotation
MCP usage
```

gerçekleştirdiğini kanıtla.

---

# 13. Codex CLI Authentication ve Zero-Cost

Sistem:

```text
ChatGPT subscription authenticated Codex CLI
```

kullanmalıdır.

Zorunlu:

```text
OPENAI_API_KEY = not required
```

Denetle:

```text
source grep
config
.env.example
README
fallback logic
subprocess invocation
```

Aşağıdakilerin otomatik fallback'i olmamalıdır:

```text
OpenAI API
Anthropic
Gemini
Groq
paid embeddings
```

Codex unavailable olduğunda beklenen:

```text
WAITING_FOR_CODEX
veya
PAUSED
```

Ücretli API'ye geçiyorsa:

```text
P0/P1 product contract violation
NO-GO
```

---

# 14. Codex Quota Failure Test

Codex process failure/rate-limit simüle et.

Beklenen:

```text
run state preserved
↓
controller crash etmez
↓
paid fallback yok
↓
WAITING_FOR_CODEX / PAUSED
↓
resume mümkün
```

---

# 15. Persistent State Machine

State machine gerçekten persist ediliyor mu?

Minimum:

```text
INIT
PREFLIGHT
CREATE_CANDIDATE
START_MESA
START_MCP
RUNNING
ANOMALY
RECHECKING
REPRODUCING
CONFIRMED_BUG
REPAIRING
VERIFYING
RESTARTING
LIVE_RECHECK
WAITING_FOR_CODEX
PAUSED
STOPPING
COMPLETED
FAILED
```

Her state illa aynı isimle olmak zorunda değildir.

Ama semantik karşılığı bulunmalıdır.

Controller kill edilip tekrar açıldığında:

```text
run kaybolmamalıdır.
```

---

# 16. Crash / Resume Testi

Test:

```text
RUNNING
↓
controller process kill
↓
restart
```

Beklenen:

```text
state restore
run metadata restore
candidate preserved
storage preserved
resume mümkün
```

Repair sırasında crash de ayrıca test edilmelidir.

---

# 17. Anomaly ≠ Bug Garantisi

İlk unexpected result:

```text
repair
```

tetiklememelidir.

Akış gerçekten:

```text
detect
↓
recheck
↓
reproduce
↓
confirm
↓
repair
```

olmalıdır.

Koddan state transition ve gate'i doğrula.

---

# 18. Reproduction Gate

Repair başlamadan önce gerçekten:

```text
stable reproduction
```

gerekiyor mu?

Sadece prompt:

```text
"önce reproduce et"
```

yeterli değildir.

Controller mümkün olduğunca bu gereksinimi enforce etmelidir.

---

# 19. PRE-FIX FAIL Gate

Bu audit'in en kritik repair testlerinden biridir.

Repair Codex:

```text
regression test oluştur
```

sonrası patch uygulamadan önce testin gerçekten FAIL ettiğini kanıtlamalıdır.

Beklenen kanıt:

```text
command
exit code
failing assertion
timestamp
evidence reference
```

Böyle bir mekanizma yoksa:

```text
repair safety incomplete
```

olarak raporla.

---

# 20. Minimal Repair

Test amaçlı kontrollü bir bug candidate içine eklenebiliyorsa:

```text
known defect
```

oluştur.

MESA-QA:

```text
detect
↓
reproduce
↓
regression FAIL
↓
repair
↓
PASS
```

yapabiliyor mu?

Repair diff kontrolü:

```text
değişen dosya sayısı
değişen satır
forbidden paths
dependency changes
migration changes
```

---

# 21. High-Risk Repair Koruması

Şu değişikliklerden biri gerektiğinde otomatik repair durmalı:

```text
auth architecture
credential handling
migration
large schema redesign
large dependency replacement
deployment secrets
```

Beklenen:

```text
NEEDS_REVIEW
```

---

# 22. Post-Repair Verification

Patch sonrası yalnız:

```text
pytest PASS
```

yeterli değildir.

Akış:

```text
targeted tests PASS
↓
MESA candidate restart
↓
real MCP
↓
same reproduction
↓
PASS
```

olmalıdır.

Sonra:

```text
RUNNING
```

durumuna geri dönmelidir.

---

# 23. Repair Commit

Verified repair sonrası:

```text
git commit
```

gerçekten oluşturulmalıdır.

Kontrol:

```text
commit SHA
message
changed files
test evidence
bug ID association
```

---

# 24. Automatic Main Merge Yasağı

Kaynakta şu tehlikeli patternleri ara:

```text
git checkout main
git switch main
git merge
git push origin main
git push --force
```

Otomatik main merge/push mekanizması olmamalıdır.

---

# 25. MESA Restart Gerçek mi?

`restart` yalnız state variable değişikliği olmamalıdır.

Gerçek MESA process:

```text
stop
PID terminate
new process
health recovery
```

yaşamalıdır.

Restart sonrası aynı QA storage kullanılmalıdır.

---

# 26. Codex Thread Rotation

Uzun testte Codex context rotation mevcut mu?

Amaç:

```text
Codex kendi context'inden hatırlamasın
MESA'dan recall etmek zorunda kalsın
```

Yeni session/thread sonrası historical/current memory testlerini doğrula.

---

# 27. Low-Resource Davranışı

Default profile gerçekten düşük kaynak tüketimine uygun mu?

Kontrol:

```text
parallelism
request cadence
Codex invocation frequency
telemetry frequency
pytest frequency
full-suite frequency
```

Ana sistem yanlışlıkla high-RPS soak'a dönüşmüşse:

```text
architecture drift
```

---

# 28. Full Suite Politikası

Her repair sonrası uzun full suite çalıştırmak yerine:

```text
regression
affected module
affected subsystem
live repro
periodic full suite
```

mantığı uygulanıyor mu?

---

# 29. Stop

```text
mesa-qa stop
```

testi yap.

Beklenen:

```text
new actions stop
Codex child stop
MCP stop
MESA QA runtime stop
state flush
evidence preserved
candidate preserved
storage preserved
```

---

# 30. Teardown

```text
mesa-qa teardown
```

doğrula.

Teardown:

```text
original MESA checkout
```

silememeli veya değiştirememelidir.

Candidate/worktree/run storage işlemleri explicit olmalıdır.

---

# 31. Path Traversal / Symlink Audit

Destructive filesystem işlemleri için:

```text
resolve()
relative_to()
allowed roots
symlink resolution
```

gibi gerçek korumalar var mı?

Şu senaryoyu test et:

```text
run path → symlink → MESA main
```

Teardown bunu silebiliyor mu?

Eğer evet:

```text
P0
NO-GO
```

---

# 32. Process Ownership

MESA-QA yalnız kendi başlattığı process'leri öldürmelidir.

Kontrol:

```text
PID tracking
process identity
command/cwd
run ownership
```

Aynı porttaki başka MESA instance'ını yanlışlıkla öldürememelidir.

---

# 33. Port Collision

Port doluyken başlat.

Beklenen:

```text
preflight fail
veya
safe alternate configured port
```

Başka process'i otomatik öldürmek kabul edilmez.

---

# 34. Fake Integration Audit

Repo genelinde ara:

```text
TODO
FIXME
NotImplemented
pass
mock
stub
fake
placeholder
hard-coded success
return True
except Exception: pass
```

Bunların üretim yolunda bulunup bulunmadığını değerlendir.

Test fixture içindeki mock'larla production fake'lerini ayır.

---

# 35. Error Handling Audit

Özellikle ara:

```python
except Exception:
    pass
```

ve:

```python
except:
    return True
```

gibi fail-open davranışları.

Critical safety operation'larda beklenen:

```text
FAIL CLOSED
```

---

# 36. Test Kalitesi

MESA-QA'nın kendi testleri gerçekten meaningful mi?

Kontrol:

```text
assertion var mı?
sadece function çağrılıyor mu?
mock everything mı?
real filesystem/worktree testleri var mı?
state recovery testleri var mı?
negative tests var mı?
```

Coverage yüzdesinden çok invariant coverage önemlidir.

---

# 37. Real Integration Smoke Test

Mümkünse aşağıdaki gerçek akışı çalıştır:

```text
doctor
↓
init
↓
candidate worktree
↓
isolated storage
↓
start candidate MESA
↓
start real MCP
↓
Codex Tester connect
↓
remember
↓
recall
↓
correct
↓
recall
↓
new session/thread
↓
recall
↓
MESA restart
↓
recall
↓
stop
↓
report
```

Mock kullanılmamalıdır.

---

# 38. Controlled Repair E2E

Mümkünse QA candidate içinde kontrollü, geri alınabilir bir bug oluştur.

Ama original main'e kesinlikle dokunma.

Ardından MESA-QA'nın:

```text
detect
reproduce
PRE-FIX FAIL
repair
POST-FIX PASS
restart
live PASS
commit
continue
```

zincirini gerçek olarak doğrula.

Bu test mümkün değilse nedenini açıkça yaz.

---

# 39. Commit History Audit

İlk implementasyon agent'ının:

```text
her önemli değişiklik sonrası commit
```

kuralına uyup uymadığını kontrol et.

Beklenen:

```text
coherent milestone commits
meaningful commit messages
no giant one-shot implementation commit
no broken milestone commits
```

---

# 40. Push Audit

Kontrol:

```text
remote
current branch
upstream
local HEAD
remote HEAD
```

Son implementasyon commitleri remote'a ulaşmış mı?

Push iddiası varsa gerçek ref ile doğrula.

---

# 41. Documentation Accuracy

README'deki tüm temel komutları gerçek CLI ile karşılaştır.

Örneğin:

```text
doctor
init
run
status
pause
resume
stop
report
teardown
```

README'de olup implementasyonda olmayan komut:

```text
documentation defect
```

Implementasyonda olup yanlış anlatılan kritik davranış da finding olmalıdır.

---

# 42. Güvenlik / Yetki Modeli

Codex subprocess invocation'larını denetle.

Özellikle:

```text
danger-full-access
--yolo
unrestricted shell
home directory write
root execution
```

gibi ayarlar olup olmadığını kontrol et.

En dar gerekli sandbox tercih edilmelidir.

---

# 43. Zero-Cost Static Audit

Repo genelinde ara:

```text
OPENAI_API_KEY
ANTHROPIC_API_KEY
GEMINI_API_KEY
GROQ_API_KEY
litellm
openai client
anthropic client
google generative API
```

Bir package bulunması tek başına fail değildir.

Ama MESA-QA runtime'ın zorunlu veya otomatik ücretli fallback olarak kullanması fail'dir.

---

# 44. Performance / Resource Audit

MESA-QA'nın kendisi MESA'dan daha ağır bir sistem haline gelmemelidir.

Takip:

```text
controller RSS
candidate MESA RSS
Codex child lifetime
polling frequency
busy loops
unbounded queue
unbounded log accumulation
```

Özellikle:

```text
while True without sleep
```

gibi busy-loop'ları ara.

---

# 45. Unbounded State Audit

Saatlerce çalışan sistemlerde kontrol:

```text
logs
in-memory event arrays
Codex output buffers
evidence cache
locks
task dictionaries
scenario history
```

sınırsız büyüyor mu?

Persistent storage kullanılabilir ama RAM unbounded olmamalıdır.

---

# 46. Scoring

100 puan:

| Alan                             |    Puan |
| -------------------------------- | ------: |
| Main / storage safety            |      15 |
| Candidate/worktree architecture  |      10 |
| Real MESA/MCP integration        |      15 |
| Tester behavior                  |      10 |
| Ground Truth + scenarios         |       8 |
| Anomaly/reproduction correctness |      10 |
| Repair safety                    |      12 |
| Restart/live verification        |       5 |
| Persistent/resumable controller  |       5 |
| Zero-extra-cost                  |       4 |
| Low-resource behavior            |       3 |
| Tests/docs/operator UX           |       3 |
| **Toplam**                       | **100** |

---

# 47. Hard Gates

Puan ne olursa olsun aşağıdakilerden biri varsa:

```text
NO-GO
```

* original MESA checkout autonomous write mümkün;
* gerçek MESA data/storage riske giriyor;
* automatic main merge/push var;
* Tester normal testte internalleri bypass ediyor;
* fake MCP integration;
* repair pre-fix failure olmadan uygulanabiliyor;
* paid API automatic fallback var;
* destructive teardown path safety bozuk;
* verified repair sonrası gerçek runtime revalidation yok;
* sistemi çalıştırmak için spesifikasyonda yasaklanmış ücretli servis zorunlu.

---

# 48. Karar Seviyeleri

```text
90–100
STRONG GO

80–89
GO

70–79
CONDITIONAL GO

50–69
NO-GO / substantial remediation

<50
NO-GO / architecture incomplete
```

Ama hard gate her zaman score'u override eder.

---

# 49. Finding Format

Her finding:

```text
ID:
Severity:
Component:
Contract:
Evidence:
How reproduced:
Why it matters:
Required fix:
Status:
```

formatını kullanmalıdır.

Severity:

```text
P0
P1
P2
P3
```

---

# 50. Final Report

Final rapor en az:

```text
1. Executive Summary

2. Exact repositories / commits audited

3. Commands executed

4. Architecture verification

5. Hard-gate results

6. P0 findings

7. P1 findings

8. P2/P3 findings

9. Real MCP E2E result

10. Repair E2E result

11. Main/storage integrity result

12. Zero-cost verification

13. Git history / push verification

14. Test suite results

15. Score

16. GO / CONDITIONAL GO / NO-GO

17. Ordered remediation plan
```

içermelidir.

---

# 51. Nihai Kabul Sorusu

Audit sonunda tek cümle ile cevap ver:

> “MESA-QA, MESA'ya sökülebilir harici bir AI test mühendisi olarak güvenli biçimde bağlanıp gerçek MCP yüzeyinden uzun süreli davranış testleri yapabiliyor, kanıtlanmış hataları yalnız izole candidate worktree üzerinde düzeltebiliyor, MESA main ve gerçek veriyi değiştirmeden repair sonrası gerçek runtime revalidation yapabiliyor mu?”

Bu sorunun kanıtlı cevabı:

```text
YES
```

değilse:

```text
GO
```

verilmemelidir.
