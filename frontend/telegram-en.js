/* English-only text at the Telegram boundary; dashboard labels stay Chinese. */
window.telegramEnglish={
 valid(text){return ![...String(text)].some(c=>/\p{L}/u.test(c)&&!/\p{Script=Latin}/u.test(c));},
 require(text,label='TG 文案'){if(!this.valid(text))throw new Error(label+'请使用英文，不能包含中文或其他非拉丁文字；系统不会自动翻译。');return text;},
 member(user){return user?.username?'@'+user.username:this.valid(user?.name||'')&&user?.name?user.name:'Member '+(user?.user_id||'');},
 group(group){return this.valid(group?.title||'')&&group?.title?group.title:'the CoinW community';},
 welcome:'Welcome, {name}! 👋\nSend coinw to check in and earn points. Use /ink to get your personal invite link.'
};
