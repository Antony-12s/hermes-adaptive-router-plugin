"""Count the distinct kinds of work in a prompt (investigate, fix, build, test, ...)."""

import re

# Each kind present is one part of the job.
ACTIONS = {
    "investigate": ("debug", "find the", "trace", "track down", "investigate", "identify", "diagnose", "root cause",
                    "ไล่", "สืบสวน", "ตามรอย", "ต้นเหตุ", "หาสาเหตุ", "ตามหา"),
    "fix": ("fix", "patch", "repair", "resolve", "correct", "แก้"),
    "build": ("add ", "implement", "build ", "create", "wire up", "เพิ่ม", "สร้าง", "พัฒนา", "ลงมือทำ"),
    "test": ("test", "regression", "เทสต์", "ทดสอบ"),
    "review": ("review", "check", "ตรวจ", "รีวิว"),
    "improve": ("refactor", "optimize", "clean up", "simplify", "restructure", "split ", "ปรับปรุง", "ปรับให้เร็ว", "แยก", "จัด"),
    "explain": ("explain", "summarize", "draft", "describe", "outline", "อธิบาย", "สรุป", "ร่าง"),
    "analyze": ("analyze", "compare", "evaluate", "recommend", "propose", "weigh", "วิเคราะห์", "เปรียบเทียบ", "เสนอ", "ประเมิน", "แนะนำ"),
}


def kinds_of_work(prompt):
    text = prompt.casefold()
    # "add a test" is one test job, not a build job plus a test job.
    text = re.sub(r"add (?:a |an |some )?(?:regression |unit )?tests?", "test", text)
    text = re.sub(r"เพิ่ม(?: unit test|เทสต์)", "เทสต์", text)
    return [kind for kind, words in ACTIONS.items() if any(word in text for word in words)]
