"""Build training/routing-seeds.jsonl: 500 synthetic routing examples (python -m training.generate_dataset).

Each row teaches two separate things:

* Difficulty: a tier from 0 (trivial) to 6 (maximum). The cost ladder in dashboard/ladder.py turns a tier
  into a model and effort. The local learner is trained to predict the tier.
* Provider: which account pool should run it. This is never learned. It follows the quota policy in
  `expected_route`, using the row's scenario (account usage, plan weights, heavyweight preference).
  The policy is written here independently of ladder.pick_provider so the tests can compare the two.

The 100 prompts from the first dataset keep their ids, text and split. Every other prompt is built
from templates and slot pools with a fixed seed. Evaluation prompts use templates that never appear in
training, so the held-out score measures unfamiliar phrasing.
"""

import json
import random
from pathlib import Path

from dashboard import ladder
from training.work_kinds import kinds_of_work

DATASET = Path(__file__).with_name("routing-seeds.jsonl")
SEED = 20261122
LIMIT = 85
ANTHROPIC, CODEX = "anthropic", "openai-codex"
PLAN_WEIGHT = {"x1": 1, "x5": 5, "x10": 10, "x20": 20}
CATEGORY = {"coding": "coding", "analysis": "analysis", "everyday": "general", "light": "general"}
MAX_TIER = len(ladder.TIERS) - 1

# (group, class, base tier, existing prompts, new train rows, new eval rows)
# A prompt with three or more kinds of work (investigate, fix, test, ...) in a coding or analysis group is one
# tier harder than the group's base tier.
GROUPS = [
    ("debug_ui", "coding", 3, 10, 12, 3),
    ("debug_backend", "coding", 3, 10, 12, 3),
    ("implement_feature", "coding", 3, 10, 12, 3),
    ("tests", "coding", 2, 10, 12, 3),
    ("refactor", "coding", 3, 10, 12, 3),
    ("complex_investigation", "coding", 4, 10, 12, 3),
    ("weather", "light", 0, 10, 12, 3),
    ("status_update", "light", 1, 10, 12, 3),
    ("summarize", "light", 1, 10, 12, 3),
    ("translate_rewrite", "light", 1, 10, 12, 3),
    ("build_error", "coding", 3, 0, 20, 5),
    ("code_review", "coding", 3, 0, 20, 5),
    ("sql_data", "coding", 2, 0, 20, 5),
    ("devops_ci", "coding", 3, 0, 20, 5),
    ("architecture_design", "analysis", 4, 0, 20, 5),
    ("planning_analysis", "analysis", 4, 0, 20, 5),
    ("explain_concept", "everyday", 2, 0, 20, 5),
    ("email_writing", "everyday", 2, 0, 20, 5),
    ("chitchat", "light", 0, 0, 20, 5),
    ("quick_fact", "light", 0, 0, 20, 5),
]

POOLS = {
    "ui": [("ปุ่มบันทึก", "the save button"), ("ตัวกรองค้นหา", "the search filter"), ("ตารางรายการ", "the results table"),
           ("หน้าต่างยืนยัน", "the confirmation dialog"), ("เมนูดรอปดาวน์", "the dropdown menu"),
           ("ช่องเลือกวันที่", "the date picker"), ("แถบด้านข้าง", "the sidebar"), ("ฟอร์มสมัครสมาชิก", "the signup form"),
           ("แท็บตั้งค่า", "the settings tab"), ("กราฟสรุปยอด", "the summary chart"),
           ("รายการแจ้งเตือน", "the notification list"), ("หน้าโปรไฟล์", "the profile page"),
           ("ช่องอัปโหลดรูป", "the image uploader"), ("แถบความคืบหน้า", "the progress bar")],
    "ui_sym": [("กดแล้วไม่มีอะไรเกิดขึ้น", "does nothing when clicked"), ("แสดงข้อมูลเก่าหลังรีเฟรช", "shows stale data after a refresh"),
               ("กะพริบตอนโหลด", "flickers while loading"), ("ค้างเมื่อกดซ้ำเร็วๆ", "freezes on rapid repeated clicks"),
               ("เพี้ยนบนหน้าจอมือถือ", "breaks on small screens"), ("ไม่อัปเดตหลังเลือกค่าใหม่", "doesn't update after a new value is chosen"),
               ("ล้นขอบเมื่อข้อความยาว", "overflows when the text is long"), ("ค่าหายหลังสลับหน้า", "loses its value after navigating away")],
    "be": [("API ล็อกอิน", "the login API"), ("เว็บฮุกชำระเงิน", "the payment webhook"), ("งานตั้งเวลารายคืน", "the nightly cron job"),
           ("จุดอัปโหลดไฟล์", "the file upload endpoint"), ("แคชของผลค้นหา", "the search cache"), ("คิวส่งอีเมล", "the email queue"),
           ("สคริปต์ย้ายฐานข้อมูล", "the database migration"), ("การรีเฟรชโทเคน", "the token refresh"),
           ("ตัวจำกัดอัตราคำขอ", "the rate limiter"), ("ตัวคำนวณส่วนลด", "the discount calculator"),
           ("ตัวแบ่งหน้าข้อมูล", "the pagination logic"), ("ตัวสร้างรายงาน", "the report generator")],
    "be_sym": [("คืนค่า 500 เป็นบางครั้ง", "returns a 500 intermittently"), ("ทำงานซ้ำสองรอบ", "runs twice"),
               ("ช้าลงมากเมื่อข้อมูลเยอะ", "slows down badly with large data"), ("เก็บข้อมูลผิดเขตเวลา", "stores the wrong timezone"),
               ("หมดเวลาเมื่อมีคำขอพร้อมกัน", "times out under concurrent requests"), ("ทำให้ข้อมูลซ้ำ", "creates duplicate records"),
               ("ตอบค่าว่างโดยไม่มี error", "returns empty results without an error")],
    "feat": [("ปุ่มส่งออก CSV", "CSV export"), ("โหมดมืด", "a dark mode toggle"), ("การค้นหาแบบเต็มข้อความ", "full-text search"),
             ("การแจ้งเตือนทางอีเมล", "email notifications"), ("ระบบแท็ก", "tagging for items"),
             ("การลากจัดลำดับรายการ", "drag-and-drop ordering"), ("หน้าประวัติการแก้ไข", "an edit history page"),
             ("การนำเข้าไฟล์ Excel", "Excel import"), ("ตัวสลับภาษา", "language switching"),
             ("การยืนยันตัวตนสองขั้น", "two-factor authentication"), ("ตัวกรองช่วงวันที่", "date-range filtering"),
             ("สิทธิ์ผู้ดูแลระบบ", "admin roles")],
    "where": [("ในหน้ารายการงาน", "in the task list"), ("ในแดชบอร์ด", "on the dashboard"), ("ในหน้าตั้งค่า", "in the settings page"),
              ("ในแอปมือถือ", "in the mobile app"), ("ในหน้ารายงาน", "on the reports page"), ("ในส่วนจัดการผู้ใช้", "in user management")],
    "fw_ui": [(x, x) for x in ("React", "Vue", "Flutter", "SwiftUI", "Next.js", "Angular")],
    "fw_be": [(x, x) for x in ("Django", "FastAPI", "Express", "Go", "Rust", "Spring Boot", "Laravel")],
    "fw_feat": [(x, x) for x in ("React", "Django", "FastAPI", "Flutter", "Laravel", "Next.js", "Spring Boot")],
    "fw_test": [(x, x) for x in ("pytest", "Jest", "JUnit", "Vitest", "XCTest", "RSpec", "PHPUnit")],
    "mod": [("ฟังก์ชันคำนวณภาษี", "the tax calculation"), ("ตัวแปลงวันที่", "the date parser"), ("ส่วนตรวจสอบอีเมล", "email validation"),
            ("ตัวแบ่งหน้าข้อมูล", "pagination"), ("ตัวจัดการคิว", "the queue handler"), ("ส่วนคิดค่าจัดส่ง", "shipping cost logic"),
            ("ตัวอ่านคอนฟิก", "the config loader"), ("ส่วนล็อกอินด้วยโทเคน", "token login"),
            ("ตัวสรุปยอดขาย", "the sales summarizer"), ("ส่วนจัดเรียงรายการ", "list sorting")],
    "inv": [("การบันทึกพร้อมกันที่ทำให้ข้อมูลผู้ใช้หาย", "concurrent saves that overwrite user data"),
            ("ยอดในรายงานที่ไม่ตรงกับฐานข้อมูล", "report totals that disagree with the database"),
            ("หน่วยความจำที่ค่อยๆ เพิ่มจนบริการล่ม", "a memory leak that slowly crashes the service"),
            ("คำขอที่ช้าเป็นช่วงๆ โดยหาสาเหตุไม่เจอ", "intermittent slow requests with no obvious cause"),
            ("งานในคิวที่ค้างเมื่อเครือข่ายไม่เสถียร", "queued jobs that pile up when the network is flaky"),
            ("การเปลี่ยนสคีมาที่อาจกระทบข้อมูลเดิม", "a schema change that may affect existing records"),
            ("deadlock ที่เกิดเฉพาะตอนโหลดสูง", "a deadlock that only appears under heavy load"),
            ("ผู้ใช้บางคนล็อกอินไม่ได้หลังย้ายเซิร์ฟเวอร์", "some users unable to log in after a server move")],
    "city": [("กรุงเทพ", "Bangkok"), ("เชียงใหม่", "Chiang Mai"), ("ภูเก็ต", "Phuket"), ("โตเกียว", "Tokyo"),
             ("ลอนดอน", "London"), ("โซล", "Seoul"), ("สิงคโปร์", "Singapore"), ("ปารีส", "Paris")],
    "when": [("วันนี้", "today"), ("พรุ่งนี้", "tomorrow"), ("คืนนี้", "tonight"), ("สุดสัปดาห์นี้", "this weekend")],
    "src": [("บันทึกข้างบน", "the notes above"), ("ข้อความในแชตนี้", "the messages in this chat"),
            ("รายการงานที่แปะไว้", "the task list I pasted"), ("log ที่ให้ไป", "the log I shared"),
            ("ตารางสถานะด้านบน", "the status table above")],
    "doc": [("อีเมลฉบับนี้", "this email"), ("รายงานการประชุมข้างบน", "the meeting notes above"), ("บทความนี้", "this article"),
            ("แชตกลุ่มนี้", "this group chat"), ("สเปกที่แปะไว้", "the spec I pasted"), ("ข้อความรีวิวเหล่านี้", "these customer reviews")],
    "fmt": [("สามข้อ", "three bullet points"), ("หนึ่งย่อหน้า", "one paragraph"), ("ประโยคเดียว", "a single sentence"),
            ("ห้าบรรทัด", "five lines")],
    "lang": [("อังกฤษ", "English"), ("ญี่ปุ่น", "Japanese"), ("ไทย", "Thai"), ("จีน", "Chinese"), ("เกาหลี", "Korean"),
             ("ฝรั่งเศส", "French")],
    "txt": [("ข้อความนี้", "this message"), ("ย่อหน้านี้", "this paragraph"), ("ประโยคนี้", "this sentence"),
            ("คำอธิบายสินค้านี้", "this product description")],
    "style": [("สุภาพ", "polite"), ("เป็นกันเอง", "casual"), ("เป็นทางการ", "formal"), ("กระชับ", "concise")],
    "bld": [("npm install ล้มเหลวเพราะ peer dependency", "npm install fails on a peer dependency conflict"),
            ("TypeScript ฟ้อง type ไม่ตรงหลังอัปเกรด", "TypeScript reports type errors after an upgrade"),
            ("Docker build หา package ไม่เจอ", "the Docker build can't find a package"),
            ("Gradle sync ล้มเหลว", "Gradle sync fails"), ("webpack รายงานว่า module not found", "webpack reports module not found"),
            ("pytest import โมดูลของโปรเจกต์ไม่เจอ", "pytest can't import a project module"),
            ("cargo build ฟ้องเรื่อง lifetime", "cargo build complains about lifetimes"),
            ("Xcode build ล้มเหลวหลังเพิ่มไลบรารี", "the Xcode build fails after adding a library"),
            ("CMake หาไลบรารีไม่เจอ", "CMake can't locate a library"), ("go build ขึ้น undefined symbol", "go build reports an undefined symbol")],
    "subj": [("พูลรีเควสต์นี้", "this pull request"), ("ฟังก์ชันที่เพิ่งเขียน", "the function I just wrote"),
             ("การเปลี่ยนแปลงในไฟล์นี้", "the changes in this file"), ("โค้ดส่วนชำระเงินนี้", "this payment code"),
             ("สคริปต์ย้ายข้อมูลนี้", "this migration script"), ("คลาสนี้", "this class")],
    "aspect": [("บั๊กหรือกรณีขอบที่หลุด", "bugs and edge cases"), ("ช่องโหว่ความปลอดภัย", "security issues"),
               ("จุดที่ทำให้ช้า", "performance problems"), ("ส่วนที่อ่านยาก", "readability problems"),
               ("จุดที่จัดการ error ไม่ดี", "error handling gaps")],
    "sql": [("ยอดขายรายเดือนแยกตามสินค้า", "monthly sales per product"), ("ลูกค้าที่ไม่สั่งซื้อใน 90 วัน", "customers with no order in 90 days"),
            ("แถวซ้ำในตารางผู้ใช้", "duplicate rows in the users table"), ("ผู้ใช้ที่ใช้งานต่อเนื่องเจ็ดวัน", "users active seven days in a row"),
            ("สินค้าขายดีสิบอันดับแรกต่อหมวด", "the top 10 products per category"),
            ("เวลารอเฉลี่ยของคำสั่งซื้อ", "the average order wait time"), ("อัตราการยกเลิกรายสัปดาห์", "the weekly cancellation rate")],
    "ops": [("pipeline CI ที่ล้มเฉพาะบน Windows", "a CI pipeline that fails only on Windows"),
            ("เวิร์กโฟลว์ GitHub Actions ที่ช้าเกินไป", "a slow GitHub Actions workflow"),
            ("Dockerfile ที่อิมเมจใหญ่เกินไป", "a Dockerfile that produces an oversized image"),
            ("ขั้นตอนดีพลอยที่ต้องย้อนกลับอัตโนมัติ", "a deploy step that needs automatic rollback"),
            ("pod ใน Kubernetes ที่รีสตาร์ตวนซ้ำ", "a Kubernetes pod stuck restarting"),
            ("สคริปต์สำรองฐานข้อมูลรายคืน", "a nightly database backup script"),
            ("การแคช dependency ใน CI", "dependency caching in CI")],
    "arch": [("ระบบแจ้งเตือนแบบเรียลไทม์", "a real-time notification system"), ("ระบบหลายผู้เช่า", "a multi-tenant system"),
             ("การแยกบริการออกจากโมโนลิธ", "splitting services out of a monolith"), ("คิวงานที่ทนต่อความล้มเหลว", "a fault-tolerant job queue"),
             ("ระบบแคชหลายชั้น", "a multi-layer cache"), ("การซิงก์ข้อมูลแบบออฟไลน์", "offline data sync"),
             ("API สาธารณะที่รองรับหลายเวอร์ชัน", "a public API that supports several versions"),
             ("ระบบจัดสรรโควต้าให้หลายบัญชี", "a quota allocation system for many accounts")],
    "plan": [("แผนย้ายฐานข้อมูลโดยไม่หยุดบริการ", "a zero-downtime database migration plan"),
             ("กลยุทธ์ราคาสำหรับแพ็กเกจใหม่", "a pricing strategy for a new plan"),
             ("แผนเปิดตัวฟีเจอร์ทีละกลุ่มผู้ใช้", "a staged feature rollout plan"),
             ("ข้อเปรียบเทียบสามทางเลือกด้านโครงสร้างระบบ", "a comparison of three architecture options"),
             ("แผนลดต้นทุนคลาวด์", "a cloud cost reduction plan"), ("ข้อเสนอเปลี่ยนกระบวนการรีวิวโค้ด", "a proposal to change the code review process"),
             ("แผนรับมือเหตุข้อมูลรั่ว", "a data breach response plan")],
    "conc": [("closure ใน JavaScript", "closures in JavaScript"), ("ความต่างของ TCP กับ UDP", "the difference between TCP and UDP"),
             ("index ในฐานข้อมูล", "database indexes"), ("เงินเฟ้อ", "inflation"), ("OAuth", "OAuth"),
             ("git rebase กับ merge", "git rebase versus merge"), ("idempotency", "idempotency"), ("ภาวะโลกร้อน", "global warming"),
             ("ภาษีมูลค่าเพิ่ม", "value-added tax"), ("DNS", "DNS"), ("ดอกเบี้ยทบต้น", "compound interest"),
             ("process กับ thread", "processes versus threads")],
    "em": [("ขอเลื่อนประชุมไปวันพฤหัส", "asking to move the meeting to Thursday"),
           ("ขอบคุณลูกค้าที่ให้ฟีดแบ็ก", "thanking a customer for their feedback"),
           ("แจ้งว่าส่งงานช้าหนึ่งวัน", "saying a deliverable will be a day late"),
           ("ปฏิเสธข้อเสนออย่างสุภาพ", "politely declining an offer"), ("ติดตามใบแจ้งหนี้ที่ยังไม่ชำระ", "following up on an unpaid invoice"),
           ("ขอให้เพื่อนร่วมทีมรีวิวโค้ด", "asking a teammate for a code review"),
           ("แนะนำตัวกับลูกค้าใหม่", "introducing myself to a new client"), ("ขอลาพักร้อนสามวัน", "requesting three days of leave")],
    "tone": [("สุภาพเป็นทางการ", "formal and polite"), ("เป็นกันเองแต่เรียบร้อย", "friendly but professional"),
             ("สั้นกระชับ", "short and direct")],
    "greet": [("สวัสดีตอนเช้า", "Good morning"), ("สวัสดีตอนเย็น", "Good evening"), ("หวัดดี", "Hey there"), ("ว่าไง", "What's up"),
              ("ขอบคุณมากนะ", "Thanks a lot"), ("ฝันดีนะ", "Good night"), ("ขอบคุณที่ช่วยเมื่อกี้", "Thanks for the help just now"),
              ("เหนื่อยจัง", "I'm tired today"), ("ดีใจจังที่งานเสร็จ", "I'm glad the work is finished"),
              ("วันนี้อารมณ์ดีมาก", "I'm in a great mood today")],
    "qa": [("เมืองหลวงของญี่ปุ่นคือเมืองอะไร", "What is the capital of Japan?"),
           ("ทองคำมีสัญลักษณ์ธาตุว่าอะไร", "What is the chemical symbol for gold?"),
           ("เดือนกุมภาพันธ์ปีอธิกสุรทินมีกี่วัน", "How many days does February have in a leap year?"),
           ("5 ไมล์เท่ากับกี่กิโลเมตร", "How many kilometres is 5 miles?"), ("15 คูณ 12 ได้เท่าไหร่", "What is 15 times 12?"),
           ("มนุษย์ไปดวงจันทร์ครั้งแรกปีไหน", "In what year did humans first land on the Moon?"),
           ("น้ำเดือดที่กี่องศาเซลเซียส", "At what temperature does water boil in Celsius?"),
           ("ดาวเคราะห์ที่ใหญ่ที่สุดในระบบสุริยะคือดวงไหน", "Which planet is the largest in the solar system?"),
           ("1 กิโลกรัมเท่ากับกี่ปอนด์", "How many pounds are in a kilogram?"),
           ("ภาษาทางการของบราซิลคือภาษาอะไร", "What is the official language of Brazil?")],
    "qa_eval": [("ภูเขาไฟฟูจิสูงกี่เมตร", "How tall is Mount Fuji in metres?"),
                ("รากที่สองของ 144 คือเท่าไหร่", "What is the square root of 144?"),
                ("แม่น้ำที่ยาวที่สุดในโลกคือแม่น้ำอะไร", "Which river is the longest in the world?"),
                ("ถ้าที่กรุงเทพเป็นเที่ยง ที่ลอนดอนกี่โมง", "If it is noon in Bangkok, what time is it in London?")],
}

# group -> {"train": {"th": [...], "en": [...]}, "eval": {"th": [...], "en": [...]}}
TEMPLATES = {
    "debug_ui": {
        "train": {"th": ["{ui}{ui_sym} ช่วยหาสาเหตุและแก้ให้หน่อย", "ใน{fw_ui} {ui}{ui_sym} ไล่บั๊กให้ที",
                         "ผู้ใช้แจ้งว่า{ui}{ui_sym} {where} ช่วยแก้และเขียนเทสต์กันถอยหลัง"],
                  "en": ["Debug why {ui} {ui_sym}.", "In our {fw_ui} app, {ui} {ui_sym} {where}. Find the cause and fix it.",
                         "Users report that {ui} {ui_sym}. Track down the bug and add a regression test."]},
        "eval": {"th": ["เจอว่า{ui}{ui_sym} ลองไล่ดูการจัดการ state แล้วแก้ที", "{where} {ui}{ui_sym} ตรวจสอบ event handler และแก้ไข"],
                 "en": ["Investigate and fix: {ui} {ui_sym} {where}.",
                        "Something is off because {ui} {ui_sym}. Please trace the state handling and patch it."]}},
    "debug_backend": {
        "train": {"th": ["{be}{be_sym} ช่วยไล่หาสาเหตุ", "ใน{fw_be} {be}{be_sym} ช่วยดูโค้ดและแก้",
                         "ลูกค้าแจ้งว่า{be}{be_sym} ตามหาต้นเหตุแล้วแก้ให้พร้อมเทสต์"],
                  "en": ["Debug why {be} {be_sym}.", "Our {fw_be} service has a bug: {be} {be_sym}. Find the root cause and fix it.",
                         "In production, {be} {be_sym}. Track it down and add a test that reproduces it."]},
        "eval": {"th": ["ตรวจ{be}ที่{be_sym} แล้วแก้ที่ต้นเหตุ", "เจอปัญหาว่า{be}{be_sym} ลองไล่ log และแก้โค้ดให้หน่อย"],
                 "en": ["Investigate and patch this: {be} {be_sym}.", "Read through the code and fix the issue where {be} {be_sym}."]}},
    "implement_feature": {
        "train": {"th": ["เพิ่ม{feat}{where}", "ช่วยทำ{feat}ด้วย {fw_feat} {where}", "อยากได้{feat} {where} ช่วยลงมือทำและเขียนเทสต์ให้"],
                  "en": ["Add {feat} {where}.", "Implement {feat} using {fw_feat}.", "We need {feat} {where}. Build it and add tests."]},
        "eval": {"th": ["ช่วยพัฒนา{feat}{where} ให้ใช้งานได้ครบ", "เพิ่มความสามารถ{feat} และอัปเดตเอกสารให้ตรงกัน"],
                 "en": ["Build {feat} {where} end to end.", "Please wire up {feat} with {fw_feat} and cover the main paths."]}},
    "tests": {
        "train": {"th": ["เขียนเทสต์ให้{mod}ครอบคลุมกรณีขอบ", "เพิ่ม unit test ของ{mod} รวมกรณีค่าว่างและค่าผิดปกติ",
                         "{mod}ยังไม่มีเทสต์ ช่วยเขียนด้วย {fw_test}"],
                  "en": ["Write unit tests for {mod} covering edge cases.", "Add tests for {mod}, including empty and invalid input.",
                         "{mod} has no coverage yet. Write tests using {fw_test}."]},
        "eval": {"th": ["ช่วยออกแบบชุดทดสอบสำหรับ{mod}และเขียนให้ครบ", "เพิ่มเทสต์กันถอยหลังให้{mod} แล้วรันให้ผ่าน"],
                 "en": ["Create a regression test suite for {mod} and make it pass.", "Cover {mod} with tests for boundary values and failure paths."]}},
    "refactor": {
        "train": {"th": ["จัด{mod}ใหม่ให้อ่านง่ายโดยไม่เปลี่ยนพฤติกรรม", "แยก{mod}ออกเป็นฟังก์ชันย่อยเพื่อให้เทสต์ง่าย",
                         "ลดโค้ดซ้ำใน{mod}และคงผลลัพธ์เดิม"],
                  "en": ["Refactor {mod} for readability without changing behavior.", "Split {mod} into smaller functions so it is easier to test.",
                         "Remove duplicated code in {mod} and keep the output identical."]},
        "eval": {"th": ["ปรับโครงสร้าง{mod}ให้เป็นโมดูลที่แยกชัดเจนและยังผ่านเทสต์เดิม", "ทำความสะอาด{mod} ตั้งชื่อใหม่ให้สื่อความและลดความซับซ้อน"],
                 "en": ["Clean up {mod}: clearer names, less nesting, same results.",
                        "Restructure {mod} into a separate module while keeping the existing tests green."]}},
    "complex_investigation": {
        "train": {"th": ["ไล่วิเคราะห์{inv} ระบุต้นเหตุก่อนแล้วเสนอวิธีแก้", "ช่วยสืบสวน{inv} ดูหลายสมมติฐานและพิสูจน์ด้วยหลักฐาน",
                         "วิเคราะห์เชิงลึกเรื่อง{inv} แล้วเขียนแผนแก้ที่ไม่กระทบระบบเดิม"],
                  "en": ["Investigate {inv}. Identify the root cause before proposing a fix.",
                         "Do a deep investigation of {inv}, testing several hypotheses against evidence.",
                         "Analyze {inv} and write a fix plan that does not disturb existing behavior."]},
        "eval": {"th": ["ตามรอยปัญหา{inv}ทั้งระบบ แล้วสรุปสาเหตุกับทางแก้ที่ปลอดภัย", "หาคำอธิบายที่น่าเชื่อถือที่สุดสำหรับ{inv} พร้อมขั้นตอนพิสูจน์"],
                 "en": ["Trace through the whole system to explain {inv}, then recommend a safe fix.",
                        "Find the most plausible explanation for {inv} and outline how to prove it."]}},
    "weather": {
        "train": {"th": ["{when}{city}อากาศเป็นยังไงบ้าง", "{when}ที่{city}ฝนจะตกไหม", "ช่วยเช็กอุณหภูมิ{when}ที่{city}หน่อย"],
                  "en": ["What's the weather in {city} {when}?", "Will it rain in {city} {when}?",
                         "How hot is it going to be in {city} {when}?"]},
        "eval": {"th": ["ต้องพกร่มไหม{when}ที่{city}", "{when}ที่{city}หนาวหรือร้อน"],
                 "en": ["Do I need an umbrella in {city} {when}?", "Give me a quick forecast for {city} {when}."]}},
    "status_update": {
        "train": {"th": ["อัปเดตความคืบหน้าจาก{src}ให้หน่อย", "จาก{src} เหลืองานอะไรที่ยังไม่เสร็จบ้าง", "เขียนสถานะสั้นๆ ส่งหัวหน้าโดยใช้{src}"],
                  "en": ["Give me a progress update based on {src}.", "From {src}, which tasks are still open?",
                         "Write a short status note for my manager using {src}."]},
        "eval": {"th": ["สรุปว่าอะไรเสร็จแล้วบ้างตาม{src}", "ทำรายงานสถานะสามบรรทัดจาก{src}"],
                 "en": ["List what has been completed according to {src}.", "Turn {src} into a three-line status report."]}},
    "summarize": {
        "train": {"th": ["สรุป{doc}เป็น{fmt}", "ช่วยจับใจความของ{doc}ให้สั้นลง", "ย่อ{doc}ให้อ่านได้ใน 30 วินาที"],
                  "en": ["Summarize {doc} in {fmt}.", "Pull out the key points of {doc}.", "Condense {doc} so I can read it in 30 seconds."]},
        "eval": {"th": ["ช่วยทำสรุปของ{doc}เป็น{fmt}", "{doc}ยาวไป ขอใจความสำคัญสั้นๆ"],
                 "en": ["Give me the gist of {doc} as {fmt}.", "Too long to read: {doc}. What are the main takeaways?"]}},
    "translate_rewrite": {
        "train": {"th": ["แปล{txt}เป็นภาษา{lang}แบบ{style}", "เรียบเรียง{txt}ใหม่ให้{style}ขึ้น", "ตรวจคำผิดและปรับสำนวน{txt}"],
                  "en": ["Translate {txt} into {lang} in a {style} tone.", "Rewrite {txt} to sound more {style}.",
                         "Proofread {txt} and fix any awkward wording."]},
        "eval": {"th": ["ช่วยแปล{txt}เป็นภาษา{lang} ให้อ่านลื่นเหมือนเจ้าของภาษา", "แก้ไวยากรณ์และการเว้นวรรคใน{txt}"],
                 "en": ["Please translate {txt} into {lang} so it reads naturally.", "Fix spelling, spacing and grammar in {txt}."]}},
    "build_error": {
        "train": {"th": ["{bld} ช่วยหาสาเหตุและแก้", "build พัง: {bld} ลองแก้ให้ผ่าน", "{bld} บนเครื่องผมแต่ CI ผ่าน ช่วยไล่ดู"],
                  "en": ["{bld}. Help me find the cause and fix it.", "Build is broken: {bld}. Get it passing again.",
                         "{bld} on my machine but CI is green. Help me work out why."]},
        "eval": {"th": ["แก้ปัญหา build นี้ให้หน่อย: {bld}", "หลังอัปเดตแพ็กเกจ {bld} ตรวจ lockfile แล้วแก้"],
                 "en": ["Fix this build failure: {bld}.", "After updating dependencies, {bld}. Check the lockfile and resolve it."]}},
    "code_review": {
        "train": {"th": ["รีวิว{subj} โดยเน้นหา{aspect}", "ช่วยตรวจ{subj}ว่ามี{aspect}ไหม", "ดู{subj}แล้วแนะนำการแก้เรื่อง{aspect}"],
                  "en": ["Review {subj}, focusing on {aspect}.", "Check {subj} for {aspect}.", "Look over {subj} and suggest fixes for {aspect}."]},
        "eval": {"th": ["ช่วยทำ code review ให้{subj} ให้ความเห็นเจาะจงทีละบรรทัด", "{subj}พร้อม merge หรือยัง ตรวจดู{aspect}ให้หน่อย"],
                 "en": ["Do a careful line-by-line review of {subj}.", "Is {subj} ready to merge? Check for {aspect}."]}},
    "sql_data": {
        "train": {"th": ["เขียน SQL หา{sql}", "ช่วยเขียนคิวรีสำหรับ{sql}และอธิบายว่าทำไมถึงถูก", "คิวรีเรื่อง{sql}ช้ามาก ช่วยปรับให้เร็วขึ้น"],
                  "en": ["Write a SQL query for {sql}.", "Help me write a query for {sql} and explain how it works.",
                         "My query for {sql} is very slow. Help me optimize it."]},
        "eval": {"th": ["ออกแบบคิวรี PostgreSQL ที่หา{sql} พร้อมดัชนีที่ควรมี", "เขียนคิวรีเรื่อง{sql}ใหม่ให้ใช้ window function"],
                 "en": ["Design a PostgreSQL query for {sql} and suggest useful indexes.", "Rewrite the query for {sql} using a window function."]}},
    "devops_ci": {
        "train": {"th": ["ช่วยแก้{ops}", "ตั้งค่า{ops}ให้เสถียรขึ้นแล้วอธิบายสิ่งที่เปลี่ยน", "ปรับปรุง{ops} ให้เร็วและปลอดภัยกว่าเดิม"],
                  "en": ["Help me fix {ops}.", "Make {ops} more reliable and explain what you changed.", "Improve {ops} so it is faster and safer."]},
        "eval": {"th": ["วิเคราะห์และแก้{ops} พร้อมอธิบายสาเหตุ", "เขียนไฟล์คอนฟิกที่ถูกต้องสำหรับ{ops}"],
                 "en": ["Diagnose and repair {ops}, then summarize the cause.", "Write the correct configuration for {ops}."]}},
    "architecture_design": {
        "train": {"th": ["ออกแบบสถาปัตยกรรมของ{arch} พร้อมข้อดีข้อเสียของแต่ละทางเลือก", "ช่วยคิดโครงสร้างระบบสำหรับ{arch} และข้อควรระวังที่ต้องวางแผนล่วงหน้า",
                         "วิเคราะห์ trade-off ในการออกแบบ{arch}"],
                  "en": ["Design the architecture for {arch} and weigh the trade-offs of each option.",
                         "Help me think through the system design of {arch}, including risks to plan for.",
                         "Analyze the trade-offs in designing {arch}."]},
        "eval": {"th": ["เสนอสถาปัตยกรรมเป้าหมายสำหรับ{arch} พร้อมเหตุผลและความเสี่ยง", "{arch}ควรออกแบบอย่างไรให้ขยายได้ในอนาคต วิเคราะห์ให้ละเอียด"],
                 "en": ["Propose a target architecture for {arch}, with reasoning and risks.",
                        "How should {arch} be designed so it can scale later? Analyze it carefully."]}},
    "planning_analysis": {
        "train": {"th": ["ช่วยร่าง{plan} แยกเป็นขั้นตอนพร้อมความเสี่ยง", "วิเคราะห์เชิงลึกเรื่อง{plan} และให้คำแนะนำพร้อมเหตุผล",
                         "เขียน{plan}ฉบับละเอียดที่ทีมเอาไปใช้ได้จริง"],
                  "en": ["Draft {plan}, broken into steps with the risks.", "Do an in-depth analysis of {plan} and recommend an approach with reasons.",
                         "Write {plan} detailed enough for the team to act on."]},
        "eval": {"th": ["ช่วยคิดและร่าง{plan}ที่พิจารณาทั้งต้นทุน เวลา และความเสี่ยง", "ประเมินข้อดีข้อเสียของ{plan} แล้วสรุปข้อเสนอที่ควรเลือก"],
                 "en": ["Develop {plan} that weighs cost, timing and risk.", "Evaluate {plan}, then recommend what we should choose."]}},
    "explain_concept": {
        "train": {"th": ["อธิบายเรื่อง{conc}ให้เข้าใจง่ายๆ", "{conc}คืออะไร ขอตัวอย่างประกอบ", "ช่วยเปรียบเทียบให้เห็นภาพเรื่อง{conc}"],
                  "en": ["Explain {conc} in simple terms.", "What is the idea behind {conc}? Give me an example.", "Help me picture {conc} with an analogy."]},
        "eval": {"th": ["ขออธิบาย{conc}แบบที่นักเรียนมัธยมก็เข้าใจ", "ช่วยสอนเรื่อง{conc}ทีละขั้น"],
                 "en": ["Teach me {conc} step by step.", "Explain {conc} so a high-school student could follow."]}},
    "email_writing": {
        "train": {"th": ["ช่วยร่างอีเมล{em} โทน{tone}", "เขียนข้อความ{em}ให้หน่อย แบบ{tone}", "อยากได้อีเมลสำหรับ{em} ใช้ภาษา{tone}"],
                  "en": ["Draft an email {em}, in a {tone} tone.", "Write a message {em}. Keep it {tone}.", "I need an email {em}. Make it {tone}."]},
        "eval": {"th": ["เขียนอีเมลสำหรับ{em} ให้ฟังดู{tone}และไม่ยาวเกินไป", "ช่วยแต่งข้อความ{em}ส่งให้ลูกค้า โทน{tone}"],
                 "en": ["Compose a professional note {em}. Tone: {tone}.", "Please write the email {em} so it sounds {tone}."]}},
    "chitchat": {
        "train": {"th": ["{greet}", "{greet} คุยเล่นหน่อยได้ไหม", "{greet} มีอะไรสนุกๆ เล่าให้ฟังไหม"],
                  "en": ["{greet}!", "{greet}. Can we chat for a minute?", "{greet}. Got anything fun to share?"]},
        "eval": {"th": ["{greet} วันนี้คุณเป็นยังไงบ้าง", "{greet} ขอคำพูดให้กำลังใจสั้นๆ หน่อย"],
                 "en": ["{greet}. How are you doing?", "{greet}. Say something encouraging, please."]}},
    "quick_fact": {
        "train": {"th": ["{qa}", "ขอถามสั้นๆ {qa}", "{qa} ตอบสั้นๆ พอ"],
                  "en": ["{qa}", "Quick question: {qa}", "{qa} A short answer is fine."]},
        "eval": {"th": ["ช่วยตอบเร็วๆ: {qa_eval}", "{qa_eval} ขอแค่คำตอบเดียว"],
                 "en": ["Just the answer, please: {qa_eval}", "{qa_eval} One line is enough."]}},
}


def fill(template, lang, rng):
    chosen, out = {}, template
    for name in sorted({part.split("}")[0] for part in template.split("{")[1:]}):
        chosen[name] = rng.choice(POOLS[name])[0 if lang == "th" else 1]
    for name, value in chosen.items():
        out = out.replace("{" + name + "}", value)
    return out[0].upper() + out[1:] if lang == "en" else out


def build_prompts(original, rng):
    """Return rows for every group: original prompts first, then new train and evaluation rows."""
    taken = {row["prompt"].casefold() for row in original}
    rows = []
    for index, (group, cls, base_tier, existing, n_train, n_eval) in enumerate(GROUPS):
        rows += [{**row, "task_group": group, "template": "seed"} for row in original if row["task_group"] == group]
        bank = TEMPLATES[group]
        for split, count in (("train", n_train), ("eval", n_eval)):
            made = 0
            while made < count:
                lang = ("th", "en")[(made + (index if split == "eval" else 0)) % 2]
                templates = bank[split][lang]
                for _ in range(300):
                    prompt = fill(templates[(made // 2) % len(templates)], lang, rng)
                    if prompt.casefold() not in taken:
                        break
                else:
                    raise RuntimeError(f"cannot find {count} distinct {split} prompts for {group}")
                taken.add(prompt.casefold())
                rows.append({"split": "train" if split == "train" else "evaluation", "task_group": group,
                             "language": lang, "prompt": prompt,
                             "template": f"{group}:{split}:{lang}:{(made // 2) % len(templates)}"})
                made += 1
    return rows


# --------------------------------------------------------------------------- quota scenarios
def left(accounts):
    return sum(PLAN_WEIGHT[a["plan"]] * (100 - a["peak"]) / 100 for a in accounts)


def hot(accounts):
    return bool(accounts) and min(a["peak"] for a in accounts) >= LIMIT


def expected_route(tier, heavyweight, accounts):
    """Provider policy: a heavyweight preference decides demanding work (tier 3 and up), otherwise the
    provider with more weighted quota runs work from tier 2 up, and a provider with no room is skipped.
    Easy work stays on Anthropic. The tier sets the model and effort."""
    other = lambda p: ANTHROPIC if p == CODEX else CODEX
    is_hot = lambda p: hot(accounts.get(p, []))
    provider = ANTHROPIC
    if heavyweight and tier >= 3:
        if not is_hot(heavyweight):
            provider = heavyweight
        elif not is_hot(other(heavyweight)):
            provider = other(heavyweight)
    elif tier >= 2 and ANTHROPIC in accounts and CODEX in accounts and not is_hot(ANTHROPIC) and not is_hot(CODEX):
        provider = CODEX if left(accounts[CODEX]) > left(accounts[ANTHROPIC]) else ANTHROPIC
    elif is_hot(ANTHROPIC) and not is_hot(CODEX):
        provider = CODEX
    return ladder.rung(provider, tier)


CASES = ["auto_anthropic_more", "auto_codex_more", "auto_weight_flip", "auto_anthropic_hot", "auto_codex_hot",
         "auto_both_hot", "auto_no_data", "auto_codex_only_data", "prefer_codex", "prefer_anthropic",
         "prefer_codex_hot", "prefer_anthropic_hot", "prefer_both_hot"]


def _accounts(rng, kind):
    """kind: 'hot' (every account at the limit), 'ok' (at least one account has room)."""
    plans = ["x1", "x5", "x10", "x20"]
    count = rng.choice([1, 1, 2])
    if kind == "hot":
        return [{"peak": rng.randint(LIMIT + 1, 99), "plan": rng.choice(plans)} for _ in range(count)]
    accounts = [{"peak": rng.randint(0, LIMIT - 5), "plan": rng.choice(plans)}]
    if count == 2:
        accounts.append({"peak": rng.randint(0, 99), "plan": rng.choice(plans)})
    rng.shuffle(accounts)
    return accounts


def make_accounts(case, rng):
    def ok():
        return _accounts(rng, "ok")

    def hot_():
        return _accounts(rng, "hot")

    def more(winner):
        loser = ANTHROPIC if winner == CODEX else CODEX
        while True:
            pair = {winner: ok(), loser: ok()}
            if left(pair[winner]) - left(pair[loser]) >= 0.4:
                return pair

    if case == "auto_anthropic_more":
        return more(ANTHROPIC)
    if case == "auto_codex_more":
        return more(CODEX)
    if case == "auto_weight_flip":
        peak = rng.randint(20, 70)
        heavy, light = rng.sample([ANTHROPIC, CODEX], 2)
        return {heavy: [{"peak": peak, "plan": rng.choice(["x10", "x20"])}],
                light: [{"peak": peak, "plan": "x1"}]}
    if case == "auto_anthropic_hot":
        return {ANTHROPIC: hot_(), CODEX: ok()}
    if case == "auto_codex_hot":
        return {ANTHROPIC: ok(), CODEX: hot_()}
    if case == "auto_both_hot" or case == "prefer_both_hot":
        return {ANTHROPIC: hot_(), CODEX: hot_()}
    if case == "auto_no_data":
        return {}
    if case == "auto_codex_only_data":
        return {CODEX: ok()}
    if case == "prefer_codex_hot":
        return {ANTHROPIC: ok(), CODEX: hot_()}
    if case == "prefer_anthropic_hot":
        return {ANTHROPIC: hot_(), CODEX: ok()}
    return {ANTHROPIC: ok(), CODEX: ok()}    # preferred provider is healthy; quota is ignored


def attach_scenarios(rows, rng):
    by_class = {}
    for row in rows:
        by_class.setdefault(row["route_class"], []).append(row)
    for cls, members in by_class.items():
        order = []
        while len(order) < len(members):
            cycle = CASES[:]
            rng.shuffle(cycle)
            order += cycle
        for row, case in zip(members, order):
            heavyweight = {"prefer_codex": CODEX, "prefer_anthropic": ANTHROPIC}.get(case.rsplit("_hot", 1)[0], "")
            if case == "prefer_both_hot":
                heavyweight = rng.choice([CODEX, ANTHROPIC])
            accounts = make_accounts(case, rng)
            row["scenario"] = {"case": case, "heavyweight": heavyweight, "limit": LIMIT,
                               "accounts": accounts,
                               "expected_route": expected_route(row["tier"], heavyweight, accounts)}


def main():
    rng = random.Random(SEED)
    existing = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    original = [row for row in existing if row["id"] <= "harp-seed-100"]
    rows = build_prompts(original, rng)
    info = {group: (cls, tier) for group, cls, tier, *_ in GROUPS}
    ids = {row["prompt"]: row["id"] for row in original}
    next_id = 101
    for row in rows:
        row["id"] = ids.get(row["prompt"]) or f"harp-seed-{next_id:03d}"
        if row["id"] not in ids.values():
            next_id += 1
        cls, tier = info[row["task_group"]]
        if cls in ("coding", "analysis") and len(kinds_of_work(row["prompt"])) >= 3:
            tier += 1
        row.update(route_class=cls, category=CATEGORY[cls], tier=min(tier, MAX_TIER), provenance="synthetic")
    rows.sort(key=lambda row: row["id"])
    attach_scenarios(rows, rng)
    keys = ["id", "split", "category", "task_group", "language", "prompt", "route_class", "tier", "template", "provenance", "scenario"]
    DATASET.write_text("".join(json.dumps({k: row[k] for k in keys}, ensure_ascii=False) + "\n" for row in rows),
                       encoding="utf-8")
    print(f"Wrote {len(rows)} rows to {DATASET}")


if __name__ == "__main__":
    main()
