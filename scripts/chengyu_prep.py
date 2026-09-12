# -*- coding: utf-8 -*-
"""성어(4자성어) 후보 뽑기 — HSK 3.0 빈도 × CC-CEDICT 관용구 표시 + 고전 성어 목록."""
import json, re, sqlite3, sys

VOW = {'a': 'āáǎàa', 'e': 'ēéěèe', 'i': 'īíǐìi', 'o': 'ōóǒòo', 'u': 'ūúǔùu', 'v': 'ǖǘǚǜü',
       'A': 'ĀÁǍÀA', 'E': 'ĒÉĚÈE', 'I': 'ĪÍǏÌI', 'O': 'ŌÓǑÒO', 'U': 'ŪÚǓÙU'}


def syl_acc(s):
    """cedict 숫자 성조(hao3) → 성조 부호(hǎo)"""
    m = re.match(r'^([A-Za-z:]+?)([1-5])$', s)
    if not m:
        return s.replace('u:', 'ü').replace('U:', 'Ü')
    body, t = m.group(1), int(m.group(2))
    body = body.replace('u:', 'v').replace('U:', 'V')
    low = body.lower()
    idx = -1
    for ch in ('a', 'e'):
        if ch in low:
            idx = low.index(ch); break
    if idx < 0 and 'ou' in low:
        idx = low.index('ou')
    if idx < 0:
        for i in range(len(low) - 1, -1, -1):
            if low[i] in 'aeiouv':
                idx = i; break
    if idx < 0:
        return body.replace('v', 'ü').replace('V', 'Ü')
    out = list(body)
    if body[idx] in VOW:
        out[idx] = VOW[body[idx]][t - 1]
    return ''.join(out).replace('v', 'ü').replace('V', 'Ü')


def acc(numeric):
    return ' '.join(syl_acc(x) for x in numeric.split())


CLASSIC = """守株待兔 画蛇添足 亡羊补牢 塞翁失马 掩耳盗铃 自相矛盾 滥竽充数 对牛弹琴 井底之蛙 杯弓蛇影
画龙点睛 破釜沉舟 卧薪尝胆 三顾茅庐 望梅止渴 指鹿为马 名落孙山 邯郸学步 东施效颦 愚公移山
刻舟求剑 拔苗助长 叶公好龙 狐假虎威 惊弓之鸟 螳螂捕蝉 一鸣惊人 完璧归赵 四面楚歌 纸上谈兵
因小失大 一箭双雕 鹤立鸡群 胸有成竹 门庭若市 洛阳纸贵 唇亡齿寒 高山流水 班门弄斧 熟能生巧
抛砖引玉 精卫填海 悬梁刺股 凿壁偷光 囊萤映雪 闻鸡起舞 温故知新 学而不厌 诲人不倦 举一反三
因材施教 循序渐进 持之以恒 锲而不舍 全力以赴 专心致志 废寝忘食 孜孜不倦 一心一意 三心二意
半途而废 功亏一篑 事半功倍 事倍功半 水滴石穿 积少成多 集思广益 取长补短 精益求精 一举两得
得不偿失 舍本逐末 本末倒置 雪中送炭 锦上添花 落井下石 同甘共苦 志同道合 一见如故 相见恨晚
推心置腹 肝胆相照 两全其美 各抒己见 众说纷纭 众志成城 齐心协力 同心同德 孤军奋战 势均力敌
青出于蓝 后来居上 名副其实 名不虚传 实事求是 脚踏实地 一丝不苟 精打细算 未雨绸缪 防患未然
见义勇为 助人为乐 舍己为人 大公无私 光明磊落 表里如一 言行一致 言而有信 一诺千金 自食其力
白手起家 艰苦奋斗 出类拔萃 才华横溢 学富五车 满腹经纶 博学多才 见多识广 目不识丁 一窍不通
不学无术 坐井观天 鼠目寸光 目光短浅 急功近利 好高骛远 眼高手低 纸醉金迷 入乡随俗 因地制宜
因势利导 随机应变 急中生智 恍然大悟 茅塞顿开 豁然开朗 举世闻名 家喻户晓 妇孺皆知 有口皆碑
众所周知 川流不息 络绎不绝 人山人海 热火朝天 兴高采烈 眉飞色舞 喜出望外 心花怒放 欢天喜地
手舞足蹈 垂头丧气 无精打采 灰心丧气 提心吊胆 忐忑不安 惊慌失措 手足无措 目瞪口呆 瞠目结舌
哭笑不得 啼笑皆非 意味深长 耐人寻味 栩栩如生 惟妙惟肖 绘声绘色 淋漓尽致 山清水秀 鸟语花香
风和日丽 一帆风顺 一路平安 万事如意 心想事成 恭喜发财 年年有余 龙马精神 蒸蒸日上 日新月异
与日俱增 层出不穷 千变万化 瞬息万变 日积月累 潜移默化 耳濡目染 根深蒂固 源远流长 博大精深
一如既往 始终如一 坚持不懈 坚定不移 不屈不挠 百折不挠 勇往直前 理直气壮 据理力争 无理取闹
强词夺理 胡说八道 信口开河 夸夸其谈 滔滔不绝 口若悬河 言简意赅 一针见血 一语道破 开门见山
直言不讳 拐弯抹角 旁敲侧击 守口如瓶 沉默寡言 默默无闻 无声无息 不动声色 泰然自若 从容不迫
临危不惧 义无反顾 奋不顾身 不遗余力 竭尽全力 全神贯注 聚精会神 目不转睛 心不在焉 密密麻麻
崇山峻岭 悬崖峭壁 一望无际 一览无余 美不胜收 目不暇接 琳琅满目 五颜六色 五光十色 姹紫嫣红""".split()


def main(out_path):
    ced = {}
    for line in open('database/cedict_full.txt', encoding='utf-8'):
        if line.startswith('#'):
            continue
        m = re.match(r'^(\S+) (\S+) \[([^\]]+)\] /(.*)/$', line.strip())
        if not m:
            continue
        trad, simp, py, gl = m.groups()
        if simp not in ced:
            ced[simp] = (trad, py, gl)

    hsk = {}
    for e in json.load(open('database/hsk/complete_hsk.json', encoding='utf-8')):
        s = e['simplified']
        if s in hsk:
            continue
        lv = e.get('level') or []
        n = 0
        for L in lv:
            m = re.search(r'(\d)$', L)
            if m and L.startswith(('new-', 'newest-')):
                n = max(n, int(m.group(1)))
        hsk[s] = (e.get('frequency', 0), n, lv)

    cands = {}
    for s, (fr, n, lv) in hsk.items():
        if len(s) == 4 and 'idiom' in ced.get(s, ('', '', ''))[2]:
            cands[s] = ('hsk', fr, n)
    for s in CLASSIC:
        if re.fullmatch(r'[一-鿿]{4}', s) and s not in cands:
            fr, n, _ = hsk.get(s, (0, 0, []))
            cands[s] = ('classic', fr, n)

    c = sqlite3.connect('database/voices.db')
    have = {r[0]: r[1] for r in c.execute("SELECT chinese,id FROM words")}
    rows = []
    for s, (src, fr, n) in cands.items():
        e = ced.get(s)
        rows.append({'cn': s, 'src': src, 'freq': fr, 'hsk': n,
                     'py': acc(e[1]) if e else '', 'num': e[1] if e else '',
                     'en': e[2] if e else '', 'in_db': have.get(s, 0), 'ced': bool(e)})
    rows.sort(key=lambda r: (-r['freq'], r['cn']))
    json.dump(rows, open(out_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('총 후보', len(rows), '| cedict 없음', sum(1 for r in rows if not r['ced']),
          '| 이미 DB', sum(1 for r in rows if r['in_db']),
          '| 신규', sum(1 for r in rows if not r['in_db']))


if __name__ == '__main__':
    main(sys.argv[1])
