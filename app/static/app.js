'use strict';
const $=id=>document.getElementById(id), tg=window.Telegram?.WebApp;
tg?.ready();tg?.expand();
const state={config:null,products:[],cart:{},category:'Все',order:null,requestKey:null,view:'tiles',location:null,matches:[]};
const money=n=>(n/100).toLocaleString('ru-RU',{maximumFractionDigits:2})+' ₽';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function toast(s){$('toast').textContent=s;$('toast').style.display='block';clearTimeout(window.toastTimer);window.toastTimer=setTimeout(()=>$('toast').style.display='none',5000)}
async function api(path,body){const r=await fetch('/api'+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-Telegram-Init-Data':tg?.initData||''},...(body===undefined?{}:{body:JSON.stringify(body)})});const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'Проверьте заполнение полей');return d}
async function run(fn){try{await fn()}catch(e){toast(e.message)}}
function show(id){for(const v of ['shopView','ordersView','orderView'])$(v).hidden=v!==id;$('cartBtn').hidden=id!=='shopView'||!count();window.scrollTo(0,0)}
function count(){return Object.keys(state.cart).length}
const weighted=p=>!!p.weighted;
const minimum=p=>p.min_qty||1;
const divisor=p=>p.divisor||1;
const quantity=(p,q)=>p.volume?(q/1000).toLocaleString('ru-RU')+' л':weighted(p)?q+' г':q+' '+(p.unit||'шт');
const lineTotal=(p,q)=>Math.round(p.price*q/divisor(p));
const priceLabel=p=>weighted(p)?money(p.price*100/divisor(p))+' / 100 г':money(p.price)+' / '+esc(p.unit);
const available=p=>p.stock===null?((weighted(p)||p.volume)?30000:30):Math.min((weighted(p)||p.volume)?30000:30,Math.floor(p.stock*divisor(p)));
const availabilityLabel=p=>available(p)>=minimum(p)?'В наличии':'Закончился';
function photo(p){return p.images?.length?`<img src="${esc(p.images[0])}" alt="${esc(p.name)}" loading="lazy" decoding="async"><span class="imagefallback" hidden>Фото недоступно</span>`:`<span>${esc(p.icon||'◈')}</span>`}
document.addEventListener('error',e=>{if(e.target.tagName==='IMG'){e.target.hidden=true;if(e.target.nextElementSibling)e.target.nextElementSibling.hidden=false}},true);
function total(){return state.products.reduce((a,p)=>a+lineTotal(p,state.cart[p.id]||0),0)}
function modify(id,n){const p=state.products.find(x=>x.id===id);if(!p)return;const old=state.cart[id]||0;let v=old?old+n*(p.step_qty||1):(n>0?minimum(p):0);if(v<minimum(p))v=0;if(v>available(p))return toast('Недостаточно товара в наличии');if(!v)delete state.cart[id];else state.cart[id]=v;state.requestKey=null;render();if($('cartDialog').open)renderCart();if($('productDialog').open)renderProduct(p)}
function stepper(p){return `<div class="stepper"><button data-change="-1" data-id="${esc(p.id)}" aria-label="Уменьшить количество">−</button><b>${quantity(p,state.cart[p.id]||0)}</b><button data-change="1" data-id="${esc(p.id)}" aria-label="Увеличить количество">+</button></div>`}
function renderProduct(p){$('productBody').innerHTML=`<div class="art detailphoto">${photo(p)}</div><h2>${esc(p.name)}</h2><p class="productdescription">${esc(p.description||'Описание пока не добавлено.')}</p><p class="availability">${availabilityLabel(p)}</p><strong>${priceLabel(p)}</strong><p>${p.volume?'От 1 л · шаг 0,5 л':weighted(p)?'От 150 г · шаг 50 г':'Количество в '+esc(p.unit)}</p><div class="buyrow">${stepper(p)}<strong>${money(lineTotal(p,state.cart[p.id]||0))}</strong></div>`}
function openProduct(id){const p=state.products.find(p=>p.id===id);if(!p)return;renderProduct(p);$('productDialog').showModal()}
function render(){const q=$('search').value.toLowerCase();$('products').classList.toggle('listview',state.view==='list');const list=state.products.filter(p=>(state.category==='Все'||categoryKeys(p).includes(state.category))&&p.name.toLowerCase().includes(q)).sort((a,b)=>Number(available(b)>=minimum(b))-Number(available(a)>=minimum(a))||(a.display_rank??1000000)-(b.display_rank??1000000));$('products').innerHTML=list.map(p=>`<article class="product"><button class="art productopen" data-product="${esc(p.id)}" aria-label="Подробнее: ${esc(p.name)}">${photo(p)}</button><button class="productname" data-product="${esc(p.id)}">${esc(p.name)}</button><small class="availability">${availabilityLabel(p)}</small><div class="buyrow"><strong class="catalogprice">${money(weighted(p)?p.price*100/divisor(p):p.price)}<small>/ ${weighted(p)?'100 г':esc(p.unit)}</small></strong><div class="productcontrols">${state.cart[p.id]?stepper(p):`<button class="plus" data-change="1" data-id="${esc(p.id)}" ${available(p)<minimum(p)?'disabled':''} aria-label="Добавить ${esc(p.name)}">+</button>`}</div></div></article>`).join('');$('empty').hidden=!!list.length;$('cartCount').textContent=count();$('cartTotal').textContent=money(total());$('cartBtn').hidden=!count()||$('shopView').hidden}

function categoryKeys(p){const path=p.category_path?.length?p.category_path:[p.category||'Каталог'];return path.map((_,i)=>JSON.stringify(path.slice(0,i+1)))}
function renderCategories(){
 const paths=state.products.map(p=>p.category_path?.length?p.category_path:[p.category||'Каталог']);
 const selected=state.category==='Все'?[]:JSON.parse(state.category);
 const button=(path,label,active)=>`<button class="${active?'active':''}" data-category="${esc(path.length?JSON.stringify(path):'Все')}" aria-pressed="${active}">${esc(label)}</button>`;
 let html='<div class="categoryrow" aria-label="Разделы">'+button([],'Все',!selected.length);
 for(const root of new Set(paths.map(p=>p[0])))html+=button([root],root,selected[0]===root);
 html+='</div>';
 for(let depth=1;depth<=selected.length;depth++){
  const prefix=selected.slice(0,depth);
  const children=[...new Set(paths.filter(p=>prefix.every((v,i)=>p[i]===v)&&p.length>depth).map(p=>p[depth]))];
  if(!children.length)break;
  html+='<div class="categoryrow subcategories" aria-label="Подразделы">'+button(prefix,'Все',selected.length===depth);
  for(const child of children)html+=button([...prefix,child],child,selected[depth]===child);
  html+='</div>';
 }
 $('categories').innerHTML=html;
}
function canPickup(sid){return !state.config.geo_required||state.matches.some(m=>m.store_id===sid)}
function selectedMinimum(){return state.config.stores.find(s=>s.id===$('store').value)?.minimum_amount??120000}
function renderLocation(){ $('nearby').hidden=!state.config.geo_required||state.config.catalog_only; $('locate').closest('.locationbox').hidden=!state.config.geo_required||state.config.catalog_only; $('locationStatus').textContent=state.location?('Сохранённая точка · точность '+Math.round(state.location.accuracy)+' м. '+(state.matches.length?'Доступно магазинов: '+state.matches.length:'Обслуживание пока не подтверждено.')):'Определите местоположение для проверки района обслуживания.'; $('clearLocation').hidden=!state.location; }
function renderStores(){$('storeOptions').innerHTML=state.config.stores.map(s=>`<button class="storeoption ${s.id===$('store').value?'selected':''}" data-store="${esc(s.id)}" ${canPickup(s.id)?'':'disabled'}><strong>${esc(s.name)}</strong><small>${esc(s.address||'')}</small></button>`).join('')}
function setView(view){state.view=view;const label=view==='tiles'?'Показать списком':'Показать плиткой';$('viewToggle').setAttribute('aria-label',label);$('viewToggle').setAttribute('title',label);$('viewToggle').innerHTML=view==='tiles'?'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M8 6h13M8 12h13M8 18h13M3 6h1M3 12h1M3 18h1"/></svg>':'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>';try{localStorage.setItem('catalogView',view)}catch{}render()}
$('viewToggle').onclick=()=>setView(state.view==='tiles'?'list':'tiles');
document.addEventListener('click',e=>{const b=e.target.closest('[data-store]');if(b){if(!canPickup(b.dataset.store))return; $('store').value=b.dataset.store;$('storePicker').open=false;run(loadStore)}else if(!e.target.closest('#storePicker'))$('storePicker').open=false});
document.addEventListener('keydown',e=>{if(e.key==='Escape')$('storePicker').open=false});

let catalogLoad=0;
async function loadStore(){const load=++catalogLoad;state.cart={};state.products=[];render();const sid=$('store').value,s=state.config.stores.find(s=>s.id===sid);$('storeAddress').textContent=s.address;$('storeName').textContent=s.name;renderStores();const products=await api('/catalog/'+encodeURIComponent(sid));if(load!==catalogLoad)return;state.products=products;for(const id of Object.keys(state.cart)){const p=state.products.find(p=>p.id===id);if(!p||p.stock===0)delete state.cart[id];else if(p.stock!==null)state.cart[id]=Math.min(state.cart[id],p.stock)}state.category='Все';state.requestKey=null;renderCategories();render()}
function renderHours(){const h=state.config.pickup_hours;$('cartHoursNotice').hidden=!h;if(h){$('cartHoursTitle').textContent='Работаем с '+h.opening+' до '+h.closing;$('cartHoursText').textContent='Время Пермское. Приём заказов до '+h.cutoff+'. '+(h.accepting?'Сборка — около '+h.assembly_minutes+' минут после оплаты.':'Сейчас оформление закрыто. Заказать можно с '+h.next_open+'.');} $('hoursNotice').textContent=h?('Работаем '+h.opening+'–'+h.closing+' · время Пермское. '+(h.accepting?'Сборка около '+h.assembly_minutes+' минут. Приём заказов до '+h.cutoff+'.':'Приём заказов закрыт. Откроемся '+h.next_open+'.')):'';}
function renderCart(){$('cartItems').innerHTML=state.products.filter(p=>state.cart[p.id]).map(p=>`<div class="cartitem"><div class="info">${esc(p.name)}<small>${quantity(p,state.cart[p.id])} · ${money(lineTotal(p,state.cart[p.id]))}</small></div>${stepper(p)}</div>`).join('');$('checkoutTotal').textContent=money(total());$('minimumNotice').textContent=total()<selectedMinimum()?'Минимальный заказ — '+money(selectedMinimum())+'. Добавьте ещё на '+money(selectedMinimum()-total()):'Минимальный заказ — '+money(selectedMinimum());$('pickupNotice').textContent=canPickup($('store').value)?'':'Для оформления подтвердите местоположение в зоне магазина.';$('submitOrder').disabled=!count()||state.config.catalog_only||total()<selectedMinimum()||!canPickup($('store').value)||(state.config.pickup_hours&&!state.config.pickup_hours.accepting)}
async function openCart(){state.config=await api('/config');renderHours();renderCart();$('checkoutForm').hidden=state.config.catalog_only;$('catalogNotice').hidden=!state.config.catalog_only;if(state.config.catalog_only){$('cartDialog').showModal();return;}$('checkoutStore').textContent=state.config.stores.find(s=>s.id===$('store').value).name;const autoTime=!!state.config.test_auto_pickup_time||!!state.config.pickup_hours;$('autoTimeNotice').textContent=state.config.pickup_hours?'Доставка примерно через '+state.config.pickup_hours.assembly_minutes+' минут после оплаты.':'';$('slotField').hidden=autoTime;$('slot').disabled=autoTime;$('slot').required=!autoTime;$('autoTimeNotice').hidden=!autoTime;if(autoTime){$('slot').innerHTML='';$('cartDialog').showModal();return;}$('slot').innerHTML='<option>Загружаем время…</option>';$('cartDialog').showModal();const times=await api('/slots/'+encodeURIComponent($('store').value));$('slot').innerHTML=times.map(t=>`<option value="${esc(t)}">${esc(t.slice(8,10)+'.'+t.slice(5,7)+' · '+t.slice(11,16))}</option>`).join('');if(!times.length){$('slot').innerHTML='<option value="">Нет доступного времени</option>';toast('Магазин пока не принимает заказы')}}
async function listOrders(){const orders=await api('/orders');$('orderCount').textContent=orders.length||'';$('ordersList').innerHTML=orders.length?orders.map(o=>`<article class="ordercard" data-order="${o.id}"><span class="badge">${esc(o.status_label)}</span><h3>${esc(o.store)} · ${money(o.amount)}</h3><p>${esc(o.items.map(i=>i.name+' × '+(i.weighted?i.selection+' г':i.qty+' '+(i.unit||'шт'))).join(', '))}</p><small>${esc(o.slot)} · #${o.id.slice(0,8)} →</small></article>`).join(''):'<div class="empty">Здесь появятся ваши заказы.<br>Выберите что-нибудь в каталоге.</div>';show('ordersView')}
async function openOrder(id,resumePayment=false){state.order=id;await refreshOrder();show('orderView');if(resumePayment&&!state.config.demo){const o=await api('/orders/'+id);if(o.status==='awaiting_payment')await pay()}}
async function refreshOrder(){if(!state.order)return;const o=await api('/orders/'+state.order);const idx=['accepted','collecting','ready','completed'].indexOf(o.status);$('orderDetail').innerHTML=`<div class="detail"><span class="badge">${esc(o.status_label)}</span><h2>${o.status==='ready'?'Можно забирать!':o.status==='completed'?'Спасибо за заказ!':'Ваш заказ'}</h2><div class="orderid">${o.id}</div><div class="progress">${[0,1,2,3].map(i=>`<i class="${i<=idx?'on':''}"></i>`).join('')}</div><strong>${esc(o.store)}</strong><p>Доставка: ${esc(o.slot)}</p><p class="muted">${esc(o.name)} · ${esc(o.phone)}</p><hr>${o.items.map(i=>`<div class="cartitem"><div class="info">${esc(i.name)} <small>${i.weighted?i.selection+' г':i.qty+' '+esc(i.unit||'шт')}</small></div><strong>${money(i.line_amount??Math.round(i.qty*i.price))}</strong></div>`).join('')}<div class="total"><span>Итого</span><strong>${money(o.amount)}</strong></div><p class="muted">${o.payment_status==='succeeded'?'Оплата подтверждена':o.payment_status==='canceled'?'Платёж отменён':'Оплата ещё не подтверждена'}</p>${o.status==='awaiting_payment'&&state.config.demo?`<button class="primary wide" id="payBtn">Тестовая оплата · без списания</button>`:''}${state.config.demo&&idx>=0&&idx<3?`<div class="demoActions"><small></small><button class="primary light" id="demoNext">${['Начать сборку','Заказ готов','Выдать заказ'][idx]}</button></div>`:''}${o.status==='attention'?'<div class="paynotice">Оплата получена. Магазин уточняет передачу заказа.</div>':''}</div>`;
$('payBtn')?.addEventListener('click',()=>run(pay));$('demoNext')?.addEventListener('click',()=>run(async()=>{await api('/orders/'+o.id+'/demo-next',{});await refreshOrder()}));
const msgs=await api('/orders/'+o.id+'/messages');$('messages').innerHTML=msgs.map(m=>`<div class="bubble ${m.sender==='customer'?'customer':''}"><small>${m.sender==='customer'?'Вы':esc('📍 '+o.store)}</small>${esc(m.text)}</div>`).join('')||'<p class="muted">Пока сообщений нет.</p>';const bot=state.config.bot_username;$('tgLink').innerHTML=bot?`<a href="https://t.me/${encodeURIComponent(bot)}" target="_blank" rel="noopener">Открыть бота</a> · Для выбора заказа отправьте <code>/order ${o.id}</code>`:''}
async function pay(){const b=$('payBtn');if(b)b.disabled=true;try{if(state.config.demo){await api('/orders/'+state.order+'/demo-pay',{});await refreshOrder()}else{const o=await api('/orders/'+state.order+'/payment',{});if(o.payment_url){if(tg?.initData)tg.openLink(o.payment_url);else window.location.assign(o.payment_url)}else await refreshOrder()}}finally{if(b)b.disabled=false}}
document.addEventListener('click',e=>{const product=e.target.closest('[data-product]');if(product)openProduct(product.dataset.product);const b=e.target.closest('[data-change]');if(b)modify(b.dataset.id,Number(b.dataset.change));const c=e.target.closest('[data-category]');if(c){state.category=c.dataset.category;renderCategories();render()}const o=e.target.closest('[data-order]');if(o)run(()=>openOrder(o.dataset.order,true))});
$('closeProduct').onclick=()=>$('productDialog').close();
$('cartBtn').onclick=()=>run(openCart);$('closeCart').onclick=()=>$('cartDialog').close();$('ordersBtn').onclick=()=>run(listOrders);$('backOrders').onclick=()=>run(listOrders);$('backShop').onclick=()=>show('shopView');$('search').oninput=render;$('store').onchange=()=>run(loadStore);$('privacyBtn').onclick=()=>$('privacyDialog').showModal();
$('checkoutForm').onsubmit=e=>{e.preventDefault();run(async()=>{const b=$('submitOrder');b.disabled=true;try{if(!canPickup($('store').value))throw Error('Подтвердите местоположение');if(total()<selectedMinimum())throw Error('Сумма меньше минимального заказа');state.requestKey??=crypto.randomUUID();let phone=$('phone').value.replace(/\D/g,'');if(phone.startsWith('8')&&phone.length===11)phone='7'+phone.slice(1);if(!/^7\d{10}$/.test(phone))throw Error('Введите телефон в формате +7 999 123-45-67');const o=await api('/orders',{store_id:$('store').value,name:$('name').value,phone:'+'+phone,address:$('address').value,slot:$('slot').value,items:Object.entries(state.cart).map(([id,qty])=>({id,qty})),request_key:state.requestKey,consent:$('consent').checked});state.cart={};state.requestKey=null;render();$('cartDialog').close();await openOrder(o.id);if(!state.config.demo)await pay()}finally{renderCart()}})};
$('chatForm').onsubmit=e=>{e.preventDefault();run(async()=>{const b=$('chatForm').querySelector('button');b.disabled=true;try{await api('/orders/'+state.order+'/messages',{text:$('chatText').value});$('chatText').value='';await refreshOrder();$('messages').scrollTop=$('messages').scrollHeight}finally{b.disabled=false}})};
async function locate(){
 if(!$('locationConsent').checked)return toast('Подтвердите сохранение местоположения');
 if(!navigator.geolocation)return toast('Геолокация недоступна. Откройте приложение на телефоне и разрешите доступ.');
 $('locate').disabled=true;
 try{
  await api('/pickup-profile/clear',{});state.location=null;state.matches=[];state.requestKey=null;renderLocation();renderStores();renderCart();
  $('locationStatus').textContent='Определяем местоположение…';
  const pos=await new Promise((resolve,reject)=>navigator.geolocation.getCurrentPosition(resolve,()=>reject(Error('Не удалось определить местоположение. Разрешите доступ и повторите.')),{enableHighAccuracy:true,timeout:20000,maximumAge:0}));
  if(!Number.isFinite(pos.coords.accuracy)||pos.coords.accuracy>100)throw Error('Недостаточная точность. Включите точную геолокацию и повторите.');
  $('locationStatus').textContent='Проверяем район обслуживания…';
  const r=await api('/pickup-profile',{lat:pos.coords.latitude,lon:pos.coords.longitude,accuracy:pos.coords.accuracy,captured_at:pos.timestamp/1000,consent:true});
  state.location=r.location;state.matches=r.matches;
  if(!canPickup($('store').value)){$('store').value=r.matches[0].store_id;await loadStore();}
 }finally{$('locate').disabled=false;renderLocation();renderStores();renderCart();}
}
$('locate').onclick=()=>run(locate);
$('nearby').textContent='◎ Моя точка';$('nearby').onclick=()=>{$('locationConsent').scrollIntoView({behavior:'smooth',block:'center'});run(locate)};
$('clearLocation').onclick=()=>run(async()=>{await api('/pickup-profile/clear',{});state.location=null;state.matches=[];state.requestKey=null;renderLocation();renderStores();renderCart()});
async function boot(){
state.config=await api('/config');renderHours();try{setView(localStorage.getItem('catalogView')==='list'?'list':'tiles')}catch{}
$('demo').hidden=true;
if(state.config.catalog_only){$('ordersBtn').hidden=true;}
if(!state.config.stores.length){$('products').innerHTML='';$('empty').hidden=false;$('empty').textContent='Загружаем каталог магазина. Страница обновится автоматически.';window.setTimeout(()=>run(boot),5000);return;}
$('empty').textContent='Товары не найдены';
$('store').innerHTML=state.config.stores.map(s=>`<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');
state.location=null;state.matches=[];try{const p=await api('/pickup-profile');state.location=p.location;state.matches=p.matches;if(p.matches.length)$('store').value=p.matches[0].store_id;}catch(e){toast(e.message)}renderLocation();
if(tg?.initDataUnsafe?.user?.first_name)$('name').value=tg.initDataUnsafe.user.first_name;
try{await loadStore()}catch(e){toast(e.message);window.setTimeout(()=>run(boot),5000);return;}const id=new URLSearchParams(location.search).get('order');if(id)await openOrder(id);
}
run(boot);
// Retry photos while the initial background download is still in progress.
setInterval(()=>{document.querySelectorAll('img[hidden]').forEach(img=>{img.hidden=false;if(img.nextElementSibling)img.nextElementSibling.hidden=true;const url=new URL(img.src);url.searchParams.set('retry',Date.now());img.src=url.href;})},30000);
setInterval(()=>{if(!$('orderView').hidden)run(refreshOrder)},15000);

setInterval(()=>run(async()=>{if(!state.config)return;const c=await api('/config');state.config.pickup_hours=c.pickup_hours;renderHours();renderCart();}),30000);
