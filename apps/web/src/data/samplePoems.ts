export interface PoemCategory {
  name: string
  type: 'work_type' | 'form' | 'style'
}

export interface Poem {
  id: number
  title: string
  author: string
  dynasty: string
  content: string
  summary: string
  categories: PoemCategory[]
}

export const samplePoems: Poem[] = [
  {
    id: 1001,
    title: '静夜思',
    author: '李白',
    dynasty: '唐',
    content: '床前明月光，疑是地上霜。\n举头望明月，低头思故乡。',
    summary: '明月照入客居，抬头与低头之间，尽是故乡。',
    categories: [
      { name: '诗', type: 'work_type' },
      { name: '五言绝句', type: 'form' },
    ],
  },
  {
    id: 1002,
    title: '春晓',
    author: '孟浩然',
    dynasty: '唐',
    content: '春眠不觉晓，处处闻啼鸟。\n夜来风雨声，花落知多少。',
    summary: '春夜醒来，只从鸟声与落花里猜度一场风雨。',
    categories: [
      { name: '诗', type: 'work_type' },
      { name: '五言绝句', type: 'form' },
    ],
  },
  {
    id: 1003,
    title: '登鹳雀楼',
    author: '王之涣',
    dynasty: '唐',
    content: '白日依山尽，黄河入海流。\n欲穷千里目，更上一层楼。',
    summary: '目力所及并非终点，再向高处便见更远的天地。',
    categories: [
      { name: '诗', type: 'work_type' },
      { name: '五言绝句', type: 'form' },
    ],
  },
  {
    id: 1004,
    title: '水调歌头·明月几时有',
    author: '苏轼',
    dynasty: '宋',
    content:
      '明月几时有？把酒问青天。\n不知天上宫阙，今夕是何年。\n我欲乘风归去，又恐琼楼玉宇，高处不胜寒。\n起舞弄清影，何似在人间。',
    summary: '借一轮明月写离合，也写旷达的人间选择。',
    categories: [
      { name: '词', type: 'work_type' },
      { name: '豪放', type: 'style' },
    ],
  },
  {
    id: 1005,
    title: '如梦令·常记溪亭日暮',
    author: '李清照',
    dynasty: '宋',
    content: '常记溪亭日暮，沉醉不知归路。\n兴尽晚回舟，误入藕花深处。\n争渡，争渡，惊起一滩鸥鹭。',
    summary: '暮色、荷塘与归舟构成一段轻盈鲜明的记忆。',
    categories: [
      { name: '词', type: 'work_type' },
      { name: '婉约', type: 'style' },
    ],
  },
  {
    id: 1006,
    title: '天净沙·秋思',
    author: '马致远',
    dynasty: '元',
    content: '枯藤老树昏鸦，小桥流水人家，古道西风瘦马。\n夕阳西下，断肠人在天涯。',
    summary: '极简的景物排列里，留下旅人孤行天涯的背影。',
    categories: [
      { name: '曲', type: 'work_type' },
      { name: '思乡', type: 'style' },
    ],
  },
]

