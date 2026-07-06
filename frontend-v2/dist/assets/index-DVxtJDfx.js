const __vite__mapDeps=(i,m=__vite__mapDeps,d=(m.f||(m.f=["assets/HomeScreenPage-N-yTQtRP.js","assets/vendor-react-CEUiQZj-.js","assets/HomePage-u1uAmGcx.js","assets/StateViews-B4DRpT-k.js","assets/flame-CxNrEXfM.js","assets/circle-play-B8RRUrB9.js","assets/trending-up-CDcb0G65.js","assets/chevron-right-BOLg1FFQ.js","assets/PoolPage-Cb2ZXxxS.js","assets/RecommendationDetailCard-6WjfD9oX.js","assets/useApi-C4suoZtF.js","assets/search-B3GgUeag.js","assets/chevron-down-BGwUCa_9.js","assets/HistoryPage-GU4kEM3p.js","assets/RecordsPage-CSHEUEjL.js","assets/arrow-left-BN4mSLH7.js","assets/MatrixPage-imJEylDp.js","assets/MetricsPage-BCKAQA53.js","assets/StrategiesPage-Dvs6zrRD.js","assets/ConfigPage-jxXaLMR5.js","assets/RunDetailPage-BfqdUr8c.js"])))=>i.map(i=>d[i]);
import{r as K_,a as kS}from"./vendor-react-CEUiQZj-.js";(function(){const e=document.createElement("link").relList;if(e&&e.supports&&e.supports("modulepreload"))return;for(const l of document.querySelectorAll('link[rel="modulepreload"]'))r(l);new MutationObserver(l=>{for(const u of l)if(u.type==="childList")for(const d of u.addedNodes)d.tagName==="LINK"&&d.rel==="modulepreload"&&r(d)}).observe(document,{childList:!0,subtree:!0});function i(l){const u={};return l.integrity&&(u.integrity=l.integrity),l.referrerPolicy&&(u.referrerPolicy=l.referrerPolicy),l.crossOrigin==="use-credentials"?u.credentials="include":l.crossOrigin==="anonymous"?u.credentials="omit":u.credentials="same-origin",u}function r(l){if(l.ep)return;l.ep=!0;const u=i(l);fetch(l.href,u)}})();var ld={exports:{}},Bo={};/**
 * @license React
 * react-jsx-runtime.production.js
 *
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */var O0;function XS(){if(O0)return Bo;O0=1;var s=Symbol.for("react.transitional.element"),e=Symbol.for("react.fragment");function i(r,l,u){var d=null;if(u!==void 0&&(d=""+u),l.key!==void 0&&(d=""+l.key),"key"in l){u={};for(var h in l)h!=="key"&&(u[h]=l[h])}else u=l;return l=u.ref,{$$typeof:s,type:r,key:d,ref:l!==void 0?l:null,props:u}}return Bo.Fragment=e,Bo.jsx=i,Bo.jsxs=i,Bo}var P0;function WS(){return P0||(P0=1,ld.exports=XS()),ld.exports}var We=WS(),mt=K_(),cd={exports:{}},Io={},ud={exports:{}},fd={};/**
 * @license React
 * scheduler.production.js
 *
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */var z0;function qS(){return z0||(z0=1,(function(s){function e(N,I){var $=N.length;N.push(I);e:for(;0<$;){var se=$-1>>>1,pe=N[se];if(0<l(pe,I))N[se]=I,N[$]=pe,$=se;else break e}}function i(N){return N.length===0?null:N[0]}function r(N){if(N.length===0)return null;var I=N[0],$=N.pop();if($!==I){N[0]=$;e:for(var se=0,pe=N.length,xe=pe>>>1;se<xe;){var Se=2*(se+1)-1,Te=N[Se],tt=Se+1,nt=N[tt];if(0>l(Te,$))tt<pe&&0>l(nt,Te)?(N[se]=nt,N[tt]=$,se=tt):(N[se]=Te,N[Se]=$,se=Se);else if(tt<pe&&0>l(nt,$))N[se]=nt,N[tt]=$,se=tt;else break e}}return I}function l(N,I){var $=N.sortIndex-I.sortIndex;return $!==0?$:N.id-I.id}if(s.unstable_now=void 0,typeof performance=="object"&&typeof performance.now=="function"){var u=performance;s.unstable_now=function(){return u.now()}}else{var d=Date,h=d.now();s.unstable_now=function(){return d.now()-h}}var m=[],p=[],v=1,x=null,g=3,E=!1,T=!1,D=!1,y=!1,S=typeof setTimeout=="function"?setTimeout:null,z=typeof clearTimeout=="function"?clearTimeout:null,F=typeof setImmediate<"u"?setImmediate:null;function C(N){for(var I=i(p);I!==null;){if(I.callback===null)r(p);else if(I.startTime<=N)r(p),I.sortIndex=I.expirationTime,e(m,I);else break;I=i(p)}}function O(N){if(D=!1,C(N),!T)if(i(m)!==null)T=!0,U||(U=!0,Z());else{var I=i(p);I!==null&&K(O,I.startTime-N)}}var U=!1,B=-1,b=5,L=-1;function W(){return y?!0:!(s.unstable_now()-L<b)}function V(){if(y=!1,U){var N=s.unstable_now();L=N;var I=!0;try{e:{T=!1,D&&(D=!1,z(B),B=-1),E=!0;var $=g;try{t:{for(C(N),x=i(m);x!==null&&!(x.expirationTime>N&&W());){var se=x.callback;if(typeof se=="function"){x.callback=null,g=x.priorityLevel;var pe=se(x.expirationTime<=N);if(N=s.unstable_now(),typeof pe=="function"){x.callback=pe,C(N),I=!0;break t}x===i(m)&&r(m),C(N)}else r(m);x=i(m)}if(x!==null)I=!0;else{var xe=i(p);xe!==null&&K(O,xe.startTime-N),I=!1}}break e}finally{x=null,g=$,E=!1}I=void 0}}finally{I?Z():U=!1}}}var Z;if(typeof F=="function")Z=function(){F(V)};else if(typeof MessageChannel<"u"){var de=new MessageChannel,he=de.port2;de.port1.onmessage=V,Z=function(){he.postMessage(null)}}else Z=function(){S(V,0)};function K(N,I){B=S(function(){N(s.unstable_now())},I)}s.unstable_IdlePriority=5,s.unstable_ImmediatePriority=1,s.unstable_LowPriority=4,s.unstable_NormalPriority=3,s.unstable_Profiling=null,s.unstable_UserBlockingPriority=2,s.unstable_cancelCallback=function(N){N.callback=null},s.unstable_forceFrameRate=function(N){0>N||125<N?console.error("forceFrameRate takes a positive int between 0 and 125, forcing frame rates higher than 125 fps is not supported"):b=0<N?Math.floor(1e3/N):5},s.unstable_getCurrentPriorityLevel=function(){return g},s.unstable_next=function(N){switch(g){case 1:case 2:case 3:var I=3;break;default:I=g}var $=g;g=I;try{return N()}finally{g=$}},s.unstable_requestPaint=function(){y=!0},s.unstable_runWithPriority=function(N,I){switch(N){case 1:case 2:case 3:case 4:case 5:break;default:N=3}var $=g;g=N;try{return I()}finally{g=$}},s.unstable_scheduleCallback=function(N,I,$){var se=s.unstable_now();switch(typeof $=="object"&&$!==null?($=$.delay,$=typeof $=="number"&&0<$?se+$:se):$=se,N){case 1:var pe=-1;break;case 2:pe=250;break;case 5:pe=1073741823;break;case 4:pe=1e4;break;default:pe=5e3}return pe=$+pe,N={id:v++,callback:I,priorityLevel:N,startTime:$,expirationTime:pe,sortIndex:-1},$>se?(N.sortIndex=$,e(p,N),i(m)===null&&N===i(p)&&(D?(z(B),B=-1):D=!0,K(O,$-se))):(N.sortIndex=pe,e(m,N),T||E||(T=!0,U||(U=!0,Z()))),N},s.unstable_shouldYield=W,s.unstable_wrapCallback=function(N){var I=g;return function(){var $=g;g=I;try{return N.apply(this,arguments)}finally{g=$}}}})(fd)),fd}var F0;function YS(){return F0||(F0=1,ud.exports=qS()),ud.exports}/**
 * @license React
 * react-dom-client.production.js
 *
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */var B0;function ZS(){if(B0)return Io;B0=1;var s=YS(),e=K_(),i=kS();function r(t){var n="https://react.dev/errors/"+t;if(1<arguments.length){n+="?args[]="+encodeURIComponent(arguments[1]);for(var a=2;a<arguments.length;a++)n+="&args[]="+encodeURIComponent(arguments[a])}return"Minified React error #"+t+"; visit "+n+" for the full message or use the non-minified dev environment for full errors and additional helpful warnings."}function l(t){return!(!t||t.nodeType!==1&&t.nodeType!==9&&t.nodeType!==11)}function u(t){var n=t,a=t;if(t.alternate)for(;n.return;)n=n.return;else{t=n;do n=t,(n.flags&4098)!==0&&(a=n.return),t=n.return;while(t)}return n.tag===3?a:null}function d(t){if(t.tag===13){var n=t.memoizedState;if(n===null&&(t=t.alternate,t!==null&&(n=t.memoizedState)),n!==null)return n.dehydrated}return null}function h(t){if(t.tag===31){var n=t.memoizedState;if(n===null&&(t=t.alternate,t!==null&&(n=t.memoizedState)),n!==null)return n.dehydrated}return null}function m(t){if(u(t)!==t)throw Error(r(188))}function p(t){var n=t.alternate;if(!n){if(n=u(t),n===null)throw Error(r(188));return n!==t?null:t}for(var a=t,o=n;;){var c=a.return;if(c===null)break;var f=c.alternate;if(f===null){if(o=c.return,o!==null){a=o;continue}break}if(c.child===f.child){for(f=c.child;f;){if(f===a)return m(c),t;if(f===o)return m(c),n;f=f.sibling}throw Error(r(188))}if(a.return!==o.return)a=c,o=f;else{for(var _=!1,R=c.child;R;){if(R===a){_=!0,a=c,o=f;break}if(R===o){_=!0,o=c,a=f;break}R=R.sibling}if(!_){for(R=f.child;R;){if(R===a){_=!0,a=f,o=c;break}if(R===o){_=!0,o=f,a=c;break}R=R.sibling}if(!_)throw Error(r(189))}}if(a.alternate!==o)throw Error(r(190))}if(a.tag!==3)throw Error(r(188));return a.stateNode.current===a?t:n}function v(t){var n=t.tag;if(n===5||n===26||n===27||n===6)return t;for(t=t.child;t!==null;){if(n=v(t),n!==null)return n;t=t.sibling}return null}var x=Object.assign,g=Symbol.for("react.element"),E=Symbol.for("react.transitional.element"),T=Symbol.for("react.portal"),D=Symbol.for("react.fragment"),y=Symbol.for("react.strict_mode"),S=Symbol.for("react.profiler"),z=Symbol.for("react.consumer"),F=Symbol.for("react.context"),C=Symbol.for("react.forward_ref"),O=Symbol.for("react.suspense"),U=Symbol.for("react.suspense_list"),B=Symbol.for("react.memo"),b=Symbol.for("react.lazy"),L=Symbol.for("react.activity"),W=Symbol.for("react.memo_cache_sentinel"),V=Symbol.iterator;function Z(t){return t===null||typeof t!="object"?null:(t=V&&t[V]||t["@@iterator"],typeof t=="function"?t:null)}var de=Symbol.for("react.client.reference");function he(t){if(t==null)return null;if(typeof t=="function")return t.$$typeof===de?null:t.displayName||t.name||null;if(typeof t=="string")return t;switch(t){case D:return"Fragment";case S:return"Profiler";case y:return"StrictMode";case O:return"Suspense";case U:return"SuspenseList";case L:return"Activity"}if(typeof t=="object")switch(t.$$typeof){case T:return"Portal";case F:return t.displayName||"Context";case z:return(t._context.displayName||"Context")+".Consumer";case C:var n=t.render;return t=t.displayName,t||(t=n.displayName||n.name||"",t=t!==""?"ForwardRef("+t+")":"ForwardRef"),t;case B:return n=t.displayName||null,n!==null?n:he(t.type)||"Memo";case b:n=t._payload,t=t._init;try{return he(t(n))}catch{}}return null}var K=Array.isArray,N=e.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE,I=i.__DOM_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE,$={pending:!1,data:null,method:null,action:null},se=[],pe=-1;function xe(t){return{current:t}}function Se(t){0>pe||(t.current=se[pe],se[pe]=null,pe--)}function Te(t,n){pe++,se[pe]=t.current,t.current=n}var tt=xe(null),nt=xe(null),ue=xe(null),Ae=xe(null);function Me(t,n){switch(Te(ue,n),Te(nt,t),Te(tt,null),n.nodeType){case 9:case 11:t=(t=n.documentElement)&&(t=t.namespaceURI)?i0(t):0;break;default:if(t=n.tagName,n=n.namespaceURI)n=i0(n),t=a0(n,t);else switch(t){case"svg":t=1;break;case"math":t=2;break;default:t=0}}Se(tt),Te(tt,t)}function $e(){Se(tt),Se(nt),Se(ue)}function at(t){t.memoizedState!==null&&Te(Ae,t);var n=tt.current,a=a0(n,t.type);n!==a&&(Te(nt,t),Te(tt,a))}function Ze(t){nt.current===t&&(Se(tt),Se(nt)),Ae.current===t&&(Se(Ae),Oo._currentValue=$)}var Yt,ut;function xt(t){if(Yt===void 0)try{throw Error()}catch(a){var n=a.stack.trim().match(/\n( *(at )?)/);Yt=n&&n[1]||"",ut=-1<a.stack.indexOf(`
    at`)?" (<anonymous>)":-1<a.stack.indexOf("@")?"@unknown:0:0":""}return`
`+Yt+t+ut}var St=!1;function ft(t,n){if(!t||St)return"";St=!0;var a=Error.prepareStackTrace;Error.prepareStackTrace=void 0;try{var o={DetermineComponentFrameRoot:function(){try{if(n){var _e=function(){throw Error()};if(Object.defineProperty(_e.prototype,"props",{set:function(){throw Error()}}),typeof Reflect=="object"&&Reflect.construct){try{Reflect.construct(_e,[])}catch(re){var ie=re}Reflect.construct(t,[],_e)}else{try{_e.call()}catch(re){ie=re}t.call(_e.prototype)}}else{try{throw Error()}catch(re){ie=re}(_e=t())&&typeof _e.catch=="function"&&_e.catch(function(){})}}catch(re){if(re&&ie&&typeof re.stack=="string")return[re.stack,ie.stack]}return[null,null]}};o.DetermineComponentFrameRoot.displayName="DetermineComponentFrameRoot";var c=Object.getOwnPropertyDescriptor(o.DetermineComponentFrameRoot,"name");c&&c.configurable&&Object.defineProperty(o.DetermineComponentFrameRoot,"name",{value:"DetermineComponentFrameRoot"});var f=o.DetermineComponentFrameRoot(),_=f[0],R=f[1];if(_&&R){var P=_.split(`
`),J=R.split(`
`);for(c=o=0;o<P.length&&!P[o].includes("DetermineComponentFrameRoot");)o++;for(;c<J.length&&!J[c].includes("DetermineComponentFrameRoot");)c++;if(o===P.length||c===J.length)for(o=P.length-1,c=J.length-1;1<=o&&0<=c&&P[o]!==J[c];)c--;for(;1<=o&&0<=c;o--,c--)if(P[o]!==J[c]){if(o!==1||c!==1)do if(o--,c--,0>c||P[o]!==J[c]){var fe=`
`+P[o].replace(" at new "," at ");return t.displayName&&fe.includes("<anonymous>")&&(fe=fe.replace("<anonymous>",t.displayName)),fe}while(1<=o&&0<=c);break}}}finally{St=!1,Error.prepareStackTrace=a}return(a=t?t.displayName||t.name:"")?xt(a):""}function en(t,n){switch(t.tag){case 26:case 27:case 5:return xt(t.type);case 16:return xt("Lazy");case 13:return t.child!==n&&n!==null?xt("Suspense Fallback"):xt("Suspense");case 19:return xt("SuspenseList");case 0:case 15:return ft(t.type,!1);case 11:return ft(t.type.render,!1);case 1:return ft(t.type,!0);case 31:return xt("Activity");default:return""}}function tn(t){try{var n="",a=null;do n+=en(t,a),a=t,t=t.return;while(t);return n}catch(o){return`
Error generating stack: `+o.message+`
`+o.stack}}var nn=Object.prototype.hasOwnProperty,ln=s.unstable_scheduleCallback,qt=s.unstable_cancelCallback,an=s.unstable_shouldYield,X=s.unstable_requestPaint,Ht=s.unstable_now,wt=s.unstable_getCurrentPriorityLevel,w=s.unstable_ImmediatePriority,M=s.unstable_UserBlockingPriority,Y=s.unstable_NormalPriority,te=s.unstable_LowPriority,le=s.unstable_IdlePriority,ye=s.log,Ce=s.unstable_setDisableYieldValue,oe=null,ce=null;function be(t){if(typeof ye=="function"&&Ce(t),ce&&typeof ce.setStrictMode=="function")try{ce.setStrictMode(oe,t)}catch{}}var Pe=Math.clz32?Math.clz32:Ye,Ue=Math.log,we=Math.LN2;function Ye(t){return t>>>=0,t===0?32:31-(Ue(t)/we|0)|0}var Ke=256,it=262144,G=4194304;function Ee(t){var n=t&42;if(n!==0)return n;switch(t&-t){case 1:return 1;case 2:return 2;case 4:return 4;case 8:return 8;case 16:return 16;case 32:return 32;case 64:return 64;case 128:return 128;case 256:case 512:case 1024:case 2048:case 4096:case 8192:case 16384:case 32768:case 65536:case 131072:return t&261888;case 262144:case 524288:case 1048576:case 2097152:return t&3932160;case 4194304:case 8388608:case 16777216:case 33554432:return t&62914560;case 67108864:return 67108864;case 134217728:return 134217728;case 268435456:return 268435456;case 536870912:return 536870912;case 1073741824:return 0;default:return t}}function me(t,n,a){var o=t.pendingLanes;if(o===0)return 0;var c=0,f=t.suspendedLanes,_=t.pingedLanes;t=t.warmLanes;var R=o&134217727;return R!==0?(o=R&~f,o!==0?c=Ee(o):(_&=R,_!==0?c=Ee(_):a||(a=R&~t,a!==0&&(c=Ee(a))))):(R=o&~f,R!==0?c=Ee(R):_!==0?c=Ee(_):a||(a=o&~t,a!==0&&(c=Ee(a)))),c===0?0:n!==0&&n!==c&&(n&f)===0&&(f=c&-c,a=n&-n,f>=a||f===32&&(a&4194048)!==0)?n:c}function Re(t,n){return(t.pendingLanes&~(t.suspendedLanes&~t.pingedLanes)&n)===0}function Oe(t,n){switch(t){case 1:case 2:case 4:case 8:case 64:return n+250;case 16:case 32:case 128:case 256:case 512:case 1024:case 2048:case 4096:case 8192:case 16384:case 32768:case 65536:case 131072:case 262144:case 524288:case 1048576:case 2097152:return n+5e3;case 4194304:case 8388608:case 16777216:case 33554432:return-1;case 67108864:case 134217728:case 268435456:case 536870912:case 1073741824:return-1;default:return-1}}function ve(){var t=G;return G<<=1,(G&62914560)===0&&(G=4194304),t}function ke(t){for(var n=[],a=0;31>a;a++)n.push(t);return n}function Ie(t,n){t.pendingLanes|=n,n!==268435456&&(t.suspendedLanes=0,t.pingedLanes=0,t.warmLanes=0)}function Qt(t,n,a,o,c,f){var _=t.pendingLanes;t.pendingLanes=a,t.suspendedLanes=0,t.pingedLanes=0,t.warmLanes=0,t.expiredLanes&=a,t.entangledLanes&=a,t.errorRecoveryDisabledLanes&=a,t.shellSuspendCounter=0;var R=t.entanglements,P=t.expirationTimes,J=t.hiddenUpdates;for(a=_&~a;0<a;){var fe=31-Pe(a),_e=1<<fe;R[fe]=0,P[fe]=-1;var ie=J[fe];if(ie!==null)for(J[fe]=null,fe=0;fe<ie.length;fe++){var re=ie[fe];re!==null&&(re.lane&=-536870913)}a&=~_e}o!==0&&Nt(t,o,0),f!==0&&c===0&&t.tag!==0&&(t.suspendedLanes|=f&~(_&~n))}function Nt(t,n,a){t.pendingLanes|=n,t.suspendedLanes&=~n;var o=31-Pe(n);t.entangledLanes|=n,t.entanglements[o]=t.entanglements[o]|1073741824|a&261930}function Kn(t,n){var a=t.entangledLanes|=n;for(t=t.entanglements;a;){var o=31-Pe(a),c=1<<o;c&n|t[o]&n&&(t[o]|=n),a&=~c}}function Qn(t,n){var a=n&-n;return a=(a&42)!==0?1:Yr(a),(a&(t.suspendedLanes|n))!==0?0:a}function Yr(t){switch(t){case 2:t=1;break;case 8:t=4;break;case 32:t=16;break;case 256:case 512:case 1024:case 2048:case 4096:case 8192:case 16384:case 32768:case 65536:case 131072:case 262144:case 524288:case 1048576:case 2097152:case 4194304:case 8388608:case 16777216:case 33554432:t=128;break;case 268435456:t=134217728;break;default:t=0}return t}function Zr(t){return t&=-t,2<t?8<t?(t&134217727)!==0?32:268435456:8:2}function Kr(){var t=I.p;return t!==0?t:(t=window.event,t===void 0?32:R0(t.type))}function ks(t,n){var a=I.p;try{return I.p=t,n()}finally{I.p=a}}var Ni=Math.random().toString(36).slice(2),dn="__reactFiber$"+Ni,Tn="__reactProps$"+Ni,Hn="__reactContainer$"+Ni,cs="__reactEvents$"+Ni,al="__reactListeners$"+Ni,sl="__reactHandles$"+Ni,us="__reactResources$"+Ni,Aa="__reactMarker$"+Ni;function Ra(t){delete t[dn],delete t[Tn],delete t[cs],delete t[al],delete t[sl]}function Ki(t){var n=t[dn];if(n)return n;for(var a=t.parentNode;a;){if(n=a[Hn]||a[dn]){if(a=n.alternate,n.child!==null||a!==null&&a.child!==null)for(t=f0(t);t!==null;){if(a=t[dn])return a;t=f0(t)}return n}t=a,a=t.parentNode}return null}function Qi(t){if(t=t[dn]||t[Hn]){var n=t.tag;if(n===5||n===6||n===13||n===31||n===26||n===27||n===3)return t}return null}function fs(t){var n=t.tag;if(n===5||n===26||n===27||n===6)return t.stateNode;throw Error(r(33))}function Ca(t){var n=t[us];return n||(n=t[us]={hoistableStyles:new Map,hoistableScripts:new Map}),n}function hn(t){t[Aa]=!0}var rl=new Set,A={};function k(t,n){ne(t,n),ne(t+"Capture",n)}function ne(t,n){for(A[t]=n,t=0;t<n.length;t++)rl.add(n[t])}var j=RegExp("^[:A-Z_a-z\\u00C0-\\u00D6\\u00D8-\\u00F6\\u00F8-\\u02FF\\u0370-\\u037D\\u037F-\\u1FFF\\u200C-\\u200D\\u2070-\\u218F\\u2C00-\\u2FEF\\u3001-\\uD7FF\\uF900-\\uFDCF\\uFDF0-\\uFFFD][:A-Z_a-z\\u00C0-\\u00D6\\u00D8-\\u00F6\\u00F8-\\u02FF\\u0370-\\u037D\\u037F-\\u1FFF\\u200C-\\u200D\\u2070-\\u218F\\u2C00-\\u2FEF\\u3001-\\uD7FF\\uF900-\\uFDCF\\uFDF0-\\uFFFD\\-.0-9\\u00B7\\u0300-\\u036F\\u203F-\\u2040]*$"),ee={},Le={};function Fe(t){return nn.call(Le,t)?!0:nn.call(ee,t)?!1:j.test(t)?Le[t]=!0:(ee[t]=!0,!1)}function De(t,n,a){if(Fe(n))if(a===null)t.removeAttribute(n);else{switch(typeof a){case"undefined":case"function":case"symbol":t.removeAttribute(n);return;case"boolean":var o=n.toLowerCase().slice(0,5);if(o!=="data-"&&o!=="aria-"){t.removeAttribute(n);return}}t.setAttribute(n,""+a)}}function Ge(t,n,a){if(a===null)t.removeAttribute(n);else{switch(typeof a){case"undefined":case"function":case"symbol":case"boolean":t.removeAttribute(n);return}t.setAttribute(n,""+a)}}function He(t,n,a,o){if(o===null)t.removeAttribute(a);else{switch(typeof o){case"undefined":case"function":case"symbol":case"boolean":t.removeAttribute(a);return}t.setAttributeNS(n,a,""+o)}}function Qe(t){switch(typeof t){case"bigint":case"boolean":case"number":case"string":case"undefined":return t;case"object":return t;default:return""}}function rt(t){var n=t.type;return(t=t.nodeName)&&t.toLowerCase()==="input"&&(n==="checkbox"||n==="radio")}function qe(t,n,a){var o=Object.getOwnPropertyDescriptor(t.constructor.prototype,n);if(!t.hasOwnProperty(n)&&typeof o<"u"&&typeof o.get=="function"&&typeof o.set=="function"){var c=o.get,f=o.set;return Object.defineProperty(t,n,{configurable:!0,get:function(){return c.call(this)},set:function(_){a=""+_,f.call(this,_)}}),Object.defineProperty(t,n,{enumerable:o.enumerable}),{getValue:function(){return a},setValue:function(_){a=""+_},stopTracking:function(){t._valueTracker=null,delete t[n]}}}}function Tt(t){if(!t._valueTracker){var n=rt(t)?"checked":"value";t._valueTracker=qe(t,n,""+t[n])}}function Jt(t){if(!t)return!1;var n=t._valueTracker;if(!n)return!0;var a=n.getValue(),o="";return t&&(o=rt(t)?t.checked?"true":"false":t.value),t=o,t!==a?(n.setValue(t),!0):!1}function Xt(t){if(t=t||(typeof document<"u"?document:void 0),typeof t>"u")return null;try{return t.activeElement||t.body}catch{return t.body}}var Ot=/[\n"\\]/g;function Pt(t){return t.replace(Ot,function(n){return"\\"+n.charCodeAt(0).toString(16)+" "})}function ze(t,n,a,o,c,f,_,R){t.name="",_!=null&&typeof _!="function"&&typeof _!="symbol"&&typeof _!="boolean"?t.type=_:t.removeAttribute("type"),n!=null?_==="number"?(n===0&&t.value===""||t.value!=n)&&(t.value=""+Qe(n)):t.value!==""+Qe(n)&&(t.value=""+Qe(n)):_!=="submit"&&_!=="reset"||t.removeAttribute("value"),n!=null?dt(t,_,Qe(n)):a!=null?dt(t,_,Qe(a)):o!=null&&t.removeAttribute("value"),c==null&&f!=null&&(t.defaultChecked=!!f),c!=null&&(t.checked=c&&typeof c!="function"&&typeof c!="symbol"),R!=null&&typeof R!="function"&&typeof R!="symbol"&&typeof R!="boolean"?t.name=""+Qe(R):t.removeAttribute("name")}function Un(t,n,a,o,c,f,_,R){if(f!=null&&typeof f!="function"&&typeof f!="symbol"&&typeof f!="boolean"&&(t.type=f),n!=null||a!=null){if(!(f!=="submit"&&f!=="reset"||n!=null)){Tt(t);return}a=a!=null?""+Qe(a):"",n=n!=null?""+Qe(n):a,R||n===t.value||(t.value=n),t.defaultValue=n}o=o??c,o=typeof o!="function"&&typeof o!="symbol"&&!!o,t.checked=R?t.checked:!!o,t.defaultChecked=!!o,_!=null&&typeof _!="function"&&typeof _!="symbol"&&typeof _!="boolean"&&(t.name=_),Tt(t)}function dt(t,n,a){n==="number"&&Xt(t.ownerDocument)===t||t.defaultValue===""+a||(t.defaultValue=""+a)}function xn(t,n,a,o){if(t=t.options,n){n={};for(var c=0;c<a.length;c++)n["$"+a[c]]=!0;for(a=0;a<t.length;a++)c=n.hasOwnProperty("$"+t[a].value),t[a].selected!==c&&(t[a].selected=c),c&&o&&(t[a].defaultSelected=!0)}else{for(a=""+Qe(a),n=null,c=0;c<t.length;c++){if(t[c].value===a){t[c].selected=!0,o&&(t[c].defaultSelected=!0);return}n!==null||t[c].disabled||(n=t[c])}n!==null&&(n.selected=!0)}}function Jn(t,n,a){if(n!=null&&(n=""+Qe(n),n!==t.value&&(t.value=n),a==null)){t.defaultValue!==n&&(t.defaultValue=n);return}t.defaultValue=a!=null?""+Qe(a):""}function Mi(t,n,a,o){if(n==null){if(o!=null){if(a!=null)throw Error(r(92));if(K(o)){if(1<o.length)throw Error(r(93));o=o[0]}a=o}a==null&&(a=""),n=a}a=Qe(n),t.defaultValue=a,o=t.textContent,o===a&&o!==""&&o!==null&&(t.value=o),Tt(t)}function $n(t,n){if(n){var a=t.firstChild;if(a&&a===t.lastChild&&a.nodeType===3){a.nodeValue=n;return}}t.textContent=n}var zt=new Set("animationIterationCount aspectRatio borderImageOutset borderImageSlice borderImageWidth boxFlex boxFlexGroup boxOrdinalGroup columnCount columns flex flexGrow flexPositive flexShrink flexNegative flexOrder gridArea gridRow gridRowEnd gridRowSpan gridRowStart gridColumn gridColumnEnd gridColumnSpan gridColumnStart fontWeight lineClamp lineHeight opacity order orphans scale tabSize widows zIndex zoom fillOpacity floodOpacity stopOpacity strokeDasharray strokeDashoffset strokeMiterlimit strokeOpacity strokeWidth MozAnimationIterationCount MozBoxFlex MozBoxFlexGroup MozLineClamp msAnimationIterationCount msFlex msZoom msFlexGrow msFlexNegative msFlexOrder msFlexPositive msFlexShrink msGridColumn msGridColumnSpan msGridRow msGridRowSpan WebkitAnimationIterationCount WebkitBoxFlex WebKitBoxFlexGroup WebkitBoxOrdinalGroup WebkitColumnCount WebkitColumns WebkitFlex WebkitFlexGrow WebkitFlexPositive WebkitFlexShrink WebkitLineClamp".split(" "));function $t(t,n,a){var o=n.indexOf("--")===0;a==null||typeof a=="boolean"||a===""?o?t.setProperty(n,""):n==="float"?t.cssFloat="":t[n]="":o?t.setProperty(n,a):typeof a!="number"||a===0||zt.has(n)?n==="float"?t.cssFloat=a:t[n]=(""+a).trim():t[n]=a+"px"}function yi(t,n,a){if(n!=null&&typeof n!="object")throw Error(r(62));if(t=t.style,a!=null){for(var o in a)!a.hasOwnProperty(o)||n!=null&&n.hasOwnProperty(o)||(o.indexOf("--")===0?t.setProperty(o,""):o==="float"?t.cssFloat="":t[o]="");for(var c in n)o=n[c],n.hasOwnProperty(c)&&a[c]!==o&&$t(t,c,o)}else for(var f in n)n.hasOwnProperty(f)&&$t(t,f,n[f])}function Lt(t){if(t.indexOf("-")===-1)return!1;switch(t){case"annotation-xml":case"color-profile":case"font-face":case"font-face-src":case"font-face-uri":case"font-face-format":case"font-face-name":case"missing-glyph":return!1;default:return!0}}var Oi=new Map([["acceptCharset","accept-charset"],["htmlFor","for"],["httpEquiv","http-equiv"],["crossOrigin","crossorigin"],["accentHeight","accent-height"],["alignmentBaseline","alignment-baseline"],["arabicForm","arabic-form"],["baselineShift","baseline-shift"],["capHeight","cap-height"],["clipPath","clip-path"],["clipRule","clip-rule"],["colorInterpolation","color-interpolation"],["colorInterpolationFilters","color-interpolation-filters"],["colorProfile","color-profile"],["colorRendering","color-rendering"],["dominantBaseline","dominant-baseline"],["enableBackground","enable-background"],["fillOpacity","fill-opacity"],["fillRule","fill-rule"],["floodColor","flood-color"],["floodOpacity","flood-opacity"],["fontFamily","font-family"],["fontSize","font-size"],["fontSizeAdjust","font-size-adjust"],["fontStretch","font-stretch"],["fontStyle","font-style"],["fontVariant","font-variant"],["fontWeight","font-weight"],["glyphName","glyph-name"],["glyphOrientationHorizontal","glyph-orientation-horizontal"],["glyphOrientationVertical","glyph-orientation-vertical"],["horizAdvX","horiz-adv-x"],["horizOriginX","horiz-origin-x"],["imageRendering","image-rendering"],["letterSpacing","letter-spacing"],["lightingColor","lighting-color"],["markerEnd","marker-end"],["markerMid","marker-mid"],["markerStart","marker-start"],["overlinePosition","overline-position"],["overlineThickness","overline-thickness"],["paintOrder","paint-order"],["panose-1","panose-1"],["pointerEvents","pointer-events"],["renderingIntent","rendering-intent"],["shapeRendering","shape-rendering"],["stopColor","stop-color"],["stopOpacity","stop-opacity"],["strikethroughPosition","strikethrough-position"],["strikethroughThickness","strikethrough-thickness"],["strokeDasharray","stroke-dasharray"],["strokeDashoffset","stroke-dashoffset"],["strokeLinecap","stroke-linecap"],["strokeLinejoin","stroke-linejoin"],["strokeMiterlimit","stroke-miterlimit"],["strokeOpacity","stroke-opacity"],["strokeWidth","stroke-width"],["textAnchor","text-anchor"],["textDecoration","text-decoration"],["textRendering","text-rendering"],["transformOrigin","transform-origin"],["underlinePosition","underline-position"],["underlineThickness","underline-thickness"],["unicodeBidi","unicode-bidi"],["unicodeRange","unicode-range"],["unitsPerEm","units-per-em"],["vAlphabetic","v-alphabetic"],["vHanging","v-hanging"],["vIdeographic","v-ideographic"],["vMathematical","v-mathematical"],["vectorEffect","vector-effect"],["vertAdvY","vert-adv-y"],["vertOriginX","vert-origin-x"],["vertOriginY","vert-origin-y"],["wordSpacing","word-spacing"],["writingMode","writing-mode"],["xmlnsXlink","xmlns:xlink"],["xHeight","x-height"]]),wa=/^[\u0000-\u001F ]*j[\r\n\t]*a[\r\n\t]*v[\r\n\t]*a[\r\n\t]*s[\r\n\t]*c[\r\n\t]*r[\r\n\t]*i[\r\n\t]*p[\r\n\t]*t[\r\n\t]*:/i;function ds(t){return wa.test(""+t)?"javascript:throw new Error('React has blocked a javascript: URL as a security precaution.')":t}function Ji(){}var au=null;function su(t){return t=t.target||t.srcElement||window,t.correspondingUseElement&&(t=t.correspondingUseElement),t.nodeType===3?t.parentNode:t}var Xs=null,Ws=null;function np(t){var n=Qi(t);if(n&&(t=n.stateNode)){var a=t[Tn]||null;e:switch(t=n.stateNode,n.type){case"input":if(ze(t,a.value,a.defaultValue,a.defaultValue,a.checked,a.defaultChecked,a.type,a.name),n=a.name,a.type==="radio"&&n!=null){for(a=t;a.parentNode;)a=a.parentNode;for(a=a.querySelectorAll('input[name="'+Pt(""+n)+'"][type="radio"]'),n=0;n<a.length;n++){var o=a[n];if(o!==t&&o.form===t.form){var c=o[Tn]||null;if(!c)throw Error(r(90));ze(o,c.value,c.defaultValue,c.defaultValue,c.checked,c.defaultChecked,c.type,c.name)}}for(n=0;n<a.length;n++)o=a[n],o.form===t.form&&Jt(o)}break e;case"textarea":Jn(t,a.value,a.defaultValue);break e;case"select":n=a.value,n!=null&&xn(t,!!a.multiple,n,!1)}}}var ru=!1;function ip(t,n,a){if(ru)return t(n,a);ru=!0;try{var o=t(n);return o}finally{if(ru=!1,(Xs!==null||Ws!==null)&&(Yl(),Xs&&(n=Xs,t=Ws,Ws=Xs=null,np(n),t)))for(n=0;n<t.length;n++)np(t[n])}}function Qr(t,n){var a=t.stateNode;if(a===null)return null;var o=a[Tn]||null;if(o===null)return null;a=o[n];e:switch(n){case"onClick":case"onClickCapture":case"onDoubleClick":case"onDoubleClickCapture":case"onMouseDown":case"onMouseDownCapture":case"onMouseMove":case"onMouseMoveCapture":case"onMouseUp":case"onMouseUpCapture":case"onMouseEnter":(o=!o.disabled)||(t=t.type,o=!(t==="button"||t==="input"||t==="select"||t==="textarea")),t=!o;break e;default:t=!1}if(t)return null;if(a&&typeof a!="function")throw Error(r(231,n,typeof a));return a}var $i=!(typeof window>"u"||typeof window.document>"u"||typeof window.document.createElement>"u"),ou=!1;if($i)try{var Jr={};Object.defineProperty(Jr,"passive",{get:function(){ou=!0}}),window.addEventListener("test",Jr,Jr),window.removeEventListener("test",Jr,Jr)}catch{ou=!1}var Da=null,lu=null,ol=null;function ap(){if(ol)return ol;var t,n=lu,a=n.length,o,c="value"in Da?Da.value:Da.textContent,f=c.length;for(t=0;t<a&&n[t]===c[t];t++);var _=a-t;for(o=1;o<=_&&n[a-o]===c[f-o];o++);return ol=c.slice(t,1<o?1-o:void 0)}function ll(t){var n=t.keyCode;return"charCode"in t?(t=t.charCode,t===0&&n===13&&(t=13)):t=n,t===10&&(t=13),32<=t||t===13?t:0}function cl(){return!0}function sp(){return!1}function Gn(t){function n(a,o,c,f,_){this._reactName=a,this._targetInst=c,this.type=o,this.nativeEvent=f,this.target=_,this.currentTarget=null;for(var R in t)t.hasOwnProperty(R)&&(a=t[R],this[R]=a?a(f):f[R]);return this.isDefaultPrevented=(f.defaultPrevented!=null?f.defaultPrevented:f.returnValue===!1)?cl:sp,this.isPropagationStopped=sp,this}return x(n.prototype,{preventDefault:function(){this.defaultPrevented=!0;var a=this.nativeEvent;a&&(a.preventDefault?a.preventDefault():typeof a.returnValue!="unknown"&&(a.returnValue=!1),this.isDefaultPrevented=cl)},stopPropagation:function(){var a=this.nativeEvent;a&&(a.stopPropagation?a.stopPropagation():typeof a.cancelBubble!="unknown"&&(a.cancelBubble=!0),this.isPropagationStopped=cl)},persist:function(){},isPersistent:cl}),n}var hs={eventPhase:0,bubbles:0,cancelable:0,timeStamp:function(t){return t.timeStamp||Date.now()},defaultPrevented:0,isTrusted:0},ul=Gn(hs),$r=x({},hs,{view:0,detail:0}),Gv=Gn($r),cu,uu,jr,fl=x({},$r,{screenX:0,screenY:0,clientX:0,clientY:0,pageX:0,pageY:0,ctrlKey:0,shiftKey:0,altKey:0,metaKey:0,getModifierState:du,button:0,buttons:0,relatedTarget:function(t){return t.relatedTarget===void 0?t.fromElement===t.srcElement?t.toElement:t.fromElement:t.relatedTarget},movementX:function(t){return"movementX"in t?t.movementX:(t!==jr&&(jr&&t.type==="mousemove"?(cu=t.screenX-jr.screenX,uu=t.screenY-jr.screenY):uu=cu=0,jr=t),cu)},movementY:function(t){return"movementY"in t?t.movementY:uu}}),rp=Gn(fl),Vv=x({},fl,{dataTransfer:0}),kv=Gn(Vv),Xv=x({},$r,{relatedTarget:0}),fu=Gn(Xv),Wv=x({},hs,{animationName:0,elapsedTime:0,pseudoElement:0}),qv=Gn(Wv),Yv=x({},hs,{clipboardData:function(t){return"clipboardData"in t?t.clipboardData:window.clipboardData}}),Zv=Gn(Yv),Kv=x({},hs,{data:0}),op=Gn(Kv),Qv={Esc:"Escape",Spacebar:" ",Left:"ArrowLeft",Up:"ArrowUp",Right:"ArrowRight",Down:"ArrowDown",Del:"Delete",Win:"OS",Menu:"ContextMenu",Apps:"ContextMenu",Scroll:"ScrollLock",MozPrintableKey:"Unidentified"},Jv={8:"Backspace",9:"Tab",12:"Clear",13:"Enter",16:"Shift",17:"Control",18:"Alt",19:"Pause",20:"CapsLock",27:"Escape",32:" ",33:"PageUp",34:"PageDown",35:"End",36:"Home",37:"ArrowLeft",38:"ArrowUp",39:"ArrowRight",40:"ArrowDown",45:"Insert",46:"Delete",112:"F1",113:"F2",114:"F3",115:"F4",116:"F5",117:"F6",118:"F7",119:"F8",120:"F9",121:"F10",122:"F11",123:"F12",144:"NumLock",145:"ScrollLock",224:"Meta"},$v={Alt:"altKey",Control:"ctrlKey",Meta:"metaKey",Shift:"shiftKey"};function jv(t){var n=this.nativeEvent;return n.getModifierState?n.getModifierState(t):(t=$v[t])?!!n[t]:!1}function du(){return jv}var ex=x({},$r,{key:function(t){if(t.key){var n=Qv[t.key]||t.key;if(n!=="Unidentified")return n}return t.type==="keypress"?(t=ll(t),t===13?"Enter":String.fromCharCode(t)):t.type==="keydown"||t.type==="keyup"?Jv[t.keyCode]||"Unidentified":""},code:0,location:0,ctrlKey:0,shiftKey:0,altKey:0,metaKey:0,repeat:0,locale:0,getModifierState:du,charCode:function(t){return t.type==="keypress"?ll(t):0},keyCode:function(t){return t.type==="keydown"||t.type==="keyup"?t.keyCode:0},which:function(t){return t.type==="keypress"?ll(t):t.type==="keydown"||t.type==="keyup"?t.keyCode:0}}),tx=Gn(ex),nx=x({},fl,{pointerId:0,width:0,height:0,pressure:0,tangentialPressure:0,tiltX:0,tiltY:0,twist:0,pointerType:0,isPrimary:0}),lp=Gn(nx),ix=x({},$r,{touches:0,targetTouches:0,changedTouches:0,altKey:0,metaKey:0,ctrlKey:0,shiftKey:0,getModifierState:du}),ax=Gn(ix),sx=x({},hs,{propertyName:0,elapsedTime:0,pseudoElement:0}),rx=Gn(sx),ox=x({},fl,{deltaX:function(t){return"deltaX"in t?t.deltaX:"wheelDeltaX"in t?-t.wheelDeltaX:0},deltaY:function(t){return"deltaY"in t?t.deltaY:"wheelDeltaY"in t?-t.wheelDeltaY:"wheelDelta"in t?-t.wheelDelta:0},deltaZ:0,deltaMode:0}),lx=Gn(ox),cx=x({},hs,{newState:0,oldState:0}),ux=Gn(cx),fx=[9,13,27,32],hu=$i&&"CompositionEvent"in window,eo=null;$i&&"documentMode"in document&&(eo=document.documentMode);var dx=$i&&"TextEvent"in window&&!eo,cp=$i&&(!hu||eo&&8<eo&&11>=eo),up=" ",fp=!1;function dp(t,n){switch(t){case"keyup":return fx.indexOf(n.keyCode)!==-1;case"keydown":return n.keyCode!==229;case"keypress":case"mousedown":case"focusout":return!0;default:return!1}}function hp(t){return t=t.detail,typeof t=="object"&&"data"in t?t.data:null}var qs=!1;function hx(t,n){switch(t){case"compositionend":return hp(n);case"keypress":return n.which!==32?null:(fp=!0,up);case"textInput":return t=n.data,t===up&&fp?null:t;default:return null}}function px(t,n){if(qs)return t==="compositionend"||!hu&&dp(t,n)?(t=ap(),ol=lu=Da=null,qs=!1,t):null;switch(t){case"paste":return null;case"keypress":if(!(n.ctrlKey||n.altKey||n.metaKey)||n.ctrlKey&&n.altKey){if(n.char&&1<n.char.length)return n.char;if(n.which)return String.fromCharCode(n.which)}return null;case"compositionend":return cp&&n.locale!=="ko"?null:n.data;default:return null}}var mx={color:!0,date:!0,datetime:!0,"datetime-local":!0,email:!0,month:!0,number:!0,password:!0,range:!0,search:!0,tel:!0,text:!0,time:!0,url:!0,week:!0};function pp(t){var n=t&&t.nodeName&&t.nodeName.toLowerCase();return n==="input"?!!mx[t.type]:n==="textarea"}function mp(t,n,a,o){Xs?Ws?Ws.push(o):Ws=[o]:Xs=o,n=ec(n,"onChange"),0<n.length&&(a=new ul("onChange","change",null,a,o),t.push({event:a,listeners:n}))}var to=null,no=null;function gx(t){Jg(t,0)}function dl(t){var n=fs(t);if(Jt(n))return t}function gp(t,n){if(t==="change")return n}var _p=!1;if($i){var pu;if($i){var mu="oninput"in document;if(!mu){var vp=document.createElement("div");vp.setAttribute("oninput","return;"),mu=typeof vp.oninput=="function"}pu=mu}else pu=!1;_p=pu&&(!document.documentMode||9<document.documentMode)}function xp(){to&&(to.detachEvent("onpropertychange",Sp),no=to=null)}function Sp(t){if(t.propertyName==="value"&&dl(no)){var n=[];mp(n,no,t,su(t)),ip(gx,n)}}function _x(t,n,a){t==="focusin"?(xp(),to=n,no=a,to.attachEvent("onpropertychange",Sp)):t==="focusout"&&xp()}function vx(t){if(t==="selectionchange"||t==="keyup"||t==="keydown")return dl(no)}function xx(t,n){if(t==="click")return dl(n)}function Sx(t,n){if(t==="input"||t==="change")return dl(n)}function Mx(t,n){return t===n&&(t!==0||1/t===1/n)||t!==t&&n!==n}var jn=typeof Object.is=="function"?Object.is:Mx;function io(t,n){if(jn(t,n))return!0;if(typeof t!="object"||t===null||typeof n!="object"||n===null)return!1;var a=Object.keys(t),o=Object.keys(n);if(a.length!==o.length)return!1;for(o=0;o<a.length;o++){var c=a[o];if(!nn.call(n,c)||!jn(t[c],n[c]))return!1}return!0}function Mp(t){for(;t&&t.firstChild;)t=t.firstChild;return t}function yp(t,n){var a=Mp(t);t=0;for(var o;a;){if(a.nodeType===3){if(o=t+a.textContent.length,t<=n&&o>=n)return{node:a,offset:n-t};t=o}e:{for(;a;){if(a.nextSibling){a=a.nextSibling;break e}a=a.parentNode}a=void 0}a=Mp(a)}}function Ep(t,n){return t&&n?t===n?!0:t&&t.nodeType===3?!1:n&&n.nodeType===3?Ep(t,n.parentNode):"contains"in t?t.contains(n):t.compareDocumentPosition?!!(t.compareDocumentPosition(n)&16):!1:!1}function bp(t){t=t!=null&&t.ownerDocument!=null&&t.ownerDocument.defaultView!=null?t.ownerDocument.defaultView:window;for(var n=Xt(t.document);n instanceof t.HTMLIFrameElement;){try{var a=typeof n.contentWindow.location.href=="string"}catch{a=!1}if(a)t=n.contentWindow;else break;n=Xt(t.document)}return n}function gu(t){var n=t&&t.nodeName&&t.nodeName.toLowerCase();return n&&(n==="input"&&(t.type==="text"||t.type==="search"||t.type==="tel"||t.type==="url"||t.type==="password")||n==="textarea"||t.contentEditable==="true")}var yx=$i&&"documentMode"in document&&11>=document.documentMode,Ys=null,_u=null,ao=null,vu=!1;function Tp(t,n,a){var o=a.window===a?a.document:a.nodeType===9?a:a.ownerDocument;vu||Ys==null||Ys!==Xt(o)||(o=Ys,"selectionStart"in o&&gu(o)?o={start:o.selectionStart,end:o.selectionEnd}:(o=(o.ownerDocument&&o.ownerDocument.defaultView||window).getSelection(),o={anchorNode:o.anchorNode,anchorOffset:o.anchorOffset,focusNode:o.focusNode,focusOffset:o.focusOffset}),ao&&io(ao,o)||(ao=o,o=ec(_u,"onSelect"),0<o.length&&(n=new ul("onSelect","select",null,n,a),t.push({event:n,listeners:o}),n.target=Ys)))}function ps(t,n){var a={};return a[t.toLowerCase()]=n.toLowerCase(),a["Webkit"+t]="webkit"+n,a["Moz"+t]="moz"+n,a}var Zs={animationend:ps("Animation","AnimationEnd"),animationiteration:ps("Animation","AnimationIteration"),animationstart:ps("Animation","AnimationStart"),transitionrun:ps("Transition","TransitionRun"),transitionstart:ps("Transition","TransitionStart"),transitioncancel:ps("Transition","TransitionCancel"),transitionend:ps("Transition","TransitionEnd")},xu={},Ap={};$i&&(Ap=document.createElement("div").style,"AnimationEvent"in window||(delete Zs.animationend.animation,delete Zs.animationiteration.animation,delete Zs.animationstart.animation),"TransitionEvent"in window||delete Zs.transitionend.transition);function ms(t){if(xu[t])return xu[t];if(!Zs[t])return t;var n=Zs[t],a;for(a in n)if(n.hasOwnProperty(a)&&a in Ap)return xu[t]=n[a];return t}var Rp=ms("animationend"),Cp=ms("animationiteration"),wp=ms("animationstart"),Ex=ms("transitionrun"),bx=ms("transitionstart"),Tx=ms("transitioncancel"),Dp=ms("transitionend"),Up=new Map,Su="abort auxClick beforeToggle cancel canPlay canPlayThrough click close contextMenu copy cut drag dragEnd dragEnter dragExit dragLeave dragOver dragStart drop durationChange emptied encrypted ended error gotPointerCapture input invalid keyDown keyPress keyUp load loadedData loadedMetadata loadStart lostPointerCapture mouseDown mouseMove mouseOut mouseOver mouseUp paste pause play playing pointerCancel pointerDown pointerMove pointerOut pointerOver pointerUp progress rateChange reset resize seeked seeking stalled submit suspend timeUpdate touchCancel touchEnd touchStart volumeChange scroll toggle touchMove waiting wheel".split(" ");Su.push("scrollEnd");function Ei(t,n){Up.set(t,n),k(n,[t])}var hl=typeof reportError=="function"?reportError:function(t){if(typeof window=="object"&&typeof window.ErrorEvent=="function"){var n=new window.ErrorEvent("error",{bubbles:!0,cancelable:!0,message:typeof t=="object"&&t!==null&&typeof t.message=="string"?String(t.message):String(t),error:t});if(!window.dispatchEvent(n))return}else if(typeof process=="object"&&typeof process.emit=="function"){process.emit("uncaughtException",t);return}console.error(t)},li=[],Ks=0,Mu=0;function pl(){for(var t=Ks,n=Mu=Ks=0;n<t;){var a=li[n];li[n++]=null;var o=li[n];li[n++]=null;var c=li[n];li[n++]=null;var f=li[n];if(li[n++]=null,o!==null&&c!==null){var _=o.pending;_===null?c.next=c:(c.next=_.next,_.next=c),o.pending=c}f!==0&&Lp(a,c,f)}}function ml(t,n,a,o){li[Ks++]=t,li[Ks++]=n,li[Ks++]=a,li[Ks++]=o,Mu|=o,t.lanes|=o,t=t.alternate,t!==null&&(t.lanes|=o)}function yu(t,n,a,o){return ml(t,n,a,o),gl(t)}function gs(t,n){return ml(t,null,null,n),gl(t)}function Lp(t,n,a){t.lanes|=a;var o=t.alternate;o!==null&&(o.lanes|=a);for(var c=!1,f=t.return;f!==null;)f.childLanes|=a,o=f.alternate,o!==null&&(o.childLanes|=a),f.tag===22&&(t=f.stateNode,t===null||t._visibility&1||(c=!0)),t=f,f=f.return;return t.tag===3?(f=t.stateNode,c&&n!==null&&(c=31-Pe(a),t=f.hiddenUpdates,o=t[c],o===null?t[c]=[n]:o.push(n),n.lane=a|536870912),f):null}function gl(t){if(50<Ro)throw Ro=0,Lf=null,Error(r(185));for(var n=t.return;n!==null;)t=n,n=t.return;return t.tag===3?t.stateNode:null}var Qs={};function Ax(t,n,a,o){this.tag=t,this.key=a,this.sibling=this.child=this.return=this.stateNode=this.type=this.elementType=null,this.index=0,this.refCleanup=this.ref=null,this.pendingProps=n,this.dependencies=this.memoizedState=this.updateQueue=this.memoizedProps=null,this.mode=o,this.subtreeFlags=this.flags=0,this.deletions=null,this.childLanes=this.lanes=0,this.alternate=null}function ei(t,n,a,o){return new Ax(t,n,a,o)}function Eu(t){return t=t.prototype,!(!t||!t.isReactComponent)}function ji(t,n){var a=t.alternate;return a===null?(a=ei(t.tag,n,t.key,t.mode),a.elementType=t.elementType,a.type=t.type,a.stateNode=t.stateNode,a.alternate=t,t.alternate=a):(a.pendingProps=n,a.type=t.type,a.flags=0,a.subtreeFlags=0,a.deletions=null),a.flags=t.flags&65011712,a.childLanes=t.childLanes,a.lanes=t.lanes,a.child=t.child,a.memoizedProps=t.memoizedProps,a.memoizedState=t.memoizedState,a.updateQueue=t.updateQueue,n=t.dependencies,a.dependencies=n===null?null:{lanes:n.lanes,firstContext:n.firstContext},a.sibling=t.sibling,a.index=t.index,a.ref=t.ref,a.refCleanup=t.refCleanup,a}function Np(t,n){t.flags&=65011714;var a=t.alternate;return a===null?(t.childLanes=0,t.lanes=n,t.child=null,t.subtreeFlags=0,t.memoizedProps=null,t.memoizedState=null,t.updateQueue=null,t.dependencies=null,t.stateNode=null):(t.childLanes=a.childLanes,t.lanes=a.lanes,t.child=a.child,t.subtreeFlags=0,t.deletions=null,t.memoizedProps=a.memoizedProps,t.memoizedState=a.memoizedState,t.updateQueue=a.updateQueue,t.type=a.type,n=a.dependencies,t.dependencies=n===null?null:{lanes:n.lanes,firstContext:n.firstContext}),t}function _l(t,n,a,o,c,f){var _=0;if(o=t,typeof t=="function")Eu(t)&&(_=1);else if(typeof t=="string")_=US(t,a,tt.current)?26:t==="html"||t==="head"||t==="body"?27:5;else e:switch(t){case L:return t=ei(31,a,n,c),t.elementType=L,t.lanes=f,t;case D:return _s(a.children,c,f,n);case y:_=8,c|=24;break;case S:return t=ei(12,a,n,c|2),t.elementType=S,t.lanes=f,t;case O:return t=ei(13,a,n,c),t.elementType=O,t.lanes=f,t;case U:return t=ei(19,a,n,c),t.elementType=U,t.lanes=f,t;default:if(typeof t=="object"&&t!==null)switch(t.$$typeof){case F:_=10;break e;case z:_=9;break e;case C:_=11;break e;case B:_=14;break e;case b:_=16,o=null;break e}_=29,a=Error(r(130,t===null?"null":typeof t,"")),o=null}return n=ei(_,a,n,c),n.elementType=t,n.type=o,n.lanes=f,n}function _s(t,n,a,o){return t=ei(7,t,o,n),t.lanes=a,t}function bu(t,n,a){return t=ei(6,t,null,n),t.lanes=a,t}function Op(t){var n=ei(18,null,null,0);return n.stateNode=t,n}function Tu(t,n,a){return n=ei(4,t.children!==null?t.children:[],t.key,n),n.lanes=a,n.stateNode={containerInfo:t.containerInfo,pendingChildren:null,implementation:t.implementation},n}var Pp=new WeakMap;function ci(t,n){if(typeof t=="object"&&t!==null){var a=Pp.get(t);return a!==void 0?a:(n={value:t,source:n,stack:tn(n)},Pp.set(t,n),n)}return{value:t,source:n,stack:tn(n)}}var Js=[],$s=0,vl=null,so=0,ui=[],fi=0,Ua=null,Pi=1,zi="";function ea(t,n){Js[$s++]=so,Js[$s++]=vl,vl=t,so=n}function zp(t,n,a){ui[fi++]=Pi,ui[fi++]=zi,ui[fi++]=Ua,Ua=t;var o=Pi;t=zi;var c=32-Pe(o)-1;o&=~(1<<c),a+=1;var f=32-Pe(n)+c;if(30<f){var _=c-c%5;f=(o&(1<<_)-1).toString(32),o>>=_,c-=_,Pi=1<<32-Pe(n)+c|a<<c|o,zi=f+t}else Pi=1<<f|a<<c|o,zi=t}function Au(t){t.return!==null&&(ea(t,1),zp(t,1,0))}function Ru(t){for(;t===vl;)vl=Js[--$s],Js[$s]=null,so=Js[--$s],Js[$s]=null;for(;t===Ua;)Ua=ui[--fi],ui[fi]=null,zi=ui[--fi],ui[fi]=null,Pi=ui[--fi],ui[fi]=null}function Fp(t,n){ui[fi++]=Pi,ui[fi++]=zi,ui[fi++]=Ua,Pi=n.id,zi=n.overflow,Ua=t}var An=null,Zt=null,Mt=!1,La=null,di=!1,Cu=Error(r(519));function Na(t){var n=Error(r(418,1<arguments.length&&arguments[1]!==void 0&&arguments[1]?"text":"HTML",""));throw ro(ci(n,t)),Cu}function Bp(t){var n=t.stateNode,a=t.type,o=t.memoizedProps;switch(n[dn]=t,n[Tn]=o,a){case"dialog":pt("cancel",n),pt("close",n);break;case"iframe":case"object":case"embed":pt("load",n);break;case"video":case"audio":for(a=0;a<wo.length;a++)pt(wo[a],n);break;case"source":pt("error",n);break;case"img":case"image":case"link":pt("error",n),pt("load",n);break;case"details":pt("toggle",n);break;case"input":pt("invalid",n),Un(n,o.value,o.defaultValue,o.checked,o.defaultChecked,o.type,o.name,!0);break;case"select":pt("invalid",n);break;case"textarea":pt("invalid",n),Mi(n,o.value,o.defaultValue,o.children)}a=o.children,typeof a!="string"&&typeof a!="number"&&typeof a!="bigint"||n.textContent===""+a||o.suppressHydrationWarning===!0||t0(n.textContent,a)?(o.popover!=null&&(pt("beforetoggle",n),pt("toggle",n)),o.onScroll!=null&&pt("scroll",n),o.onScrollEnd!=null&&pt("scrollend",n),o.onClick!=null&&(n.onclick=Ji),n=!0):n=!1,n||Na(t,!0)}function Ip(t){for(An=t.return;An;)switch(An.tag){case 5:case 31:case 13:di=!1;return;case 27:case 3:di=!0;return;default:An=An.return}}function js(t){if(t!==An)return!1;if(!Mt)return Ip(t),Mt=!0,!1;var n=t.tag,a;if((a=n!==3&&n!==27)&&((a=n===5)&&(a=t.type,a=!(a!=="form"&&a!=="button")||Yf(t.type,t.memoizedProps)),a=!a),a&&Zt&&Na(t),Ip(t),n===13){if(t=t.memoizedState,t=t!==null?t.dehydrated:null,!t)throw Error(r(317));Zt=u0(t)}else if(n===31){if(t=t.memoizedState,t=t!==null?t.dehydrated:null,!t)throw Error(r(317));Zt=u0(t)}else n===27?(n=Zt,Ya(t.type)?(t=$f,$f=null,Zt=t):Zt=n):Zt=An?pi(t.stateNode.nextSibling):null;return!0}function vs(){Zt=An=null,Mt=!1}function wu(){var t=La;return t!==null&&(Wn===null?Wn=t:Wn.push.apply(Wn,t),La=null),t}function ro(t){La===null?La=[t]:La.push(t)}var Du=xe(null),xs=null,ta=null;function Oa(t,n,a){Te(Du,n._currentValue),n._currentValue=a}function na(t){t._currentValue=Du.current,Se(Du)}function Uu(t,n,a){for(;t!==null;){var o=t.alternate;if((t.childLanes&n)!==n?(t.childLanes|=n,o!==null&&(o.childLanes|=n)):o!==null&&(o.childLanes&n)!==n&&(o.childLanes|=n),t===a)break;t=t.return}}function Lu(t,n,a,o){var c=t.child;for(c!==null&&(c.return=t);c!==null;){var f=c.dependencies;if(f!==null){var _=c.child;f=f.firstContext;e:for(;f!==null;){var R=f;f=c;for(var P=0;P<n.length;P++)if(R.context===n[P]){f.lanes|=a,R=f.alternate,R!==null&&(R.lanes|=a),Uu(f.return,a,t),o||(_=null);break e}f=R.next}}else if(c.tag===18){if(_=c.return,_===null)throw Error(r(341));_.lanes|=a,f=_.alternate,f!==null&&(f.lanes|=a),Uu(_,a,t),_=null}else _=c.child;if(_!==null)_.return=c;else for(_=c;_!==null;){if(_===t){_=null;break}if(c=_.sibling,c!==null){c.return=_.return,_=c;break}_=_.return}c=_}}function er(t,n,a,o){t=null;for(var c=n,f=!1;c!==null;){if(!f){if((c.flags&524288)!==0)f=!0;else if((c.flags&262144)!==0)break}if(c.tag===10){var _=c.alternate;if(_===null)throw Error(r(387));if(_=_.memoizedProps,_!==null){var R=c.type;jn(c.pendingProps.value,_.value)||(t!==null?t.push(R):t=[R])}}else if(c===Ae.current){if(_=c.alternate,_===null)throw Error(r(387));_.memoizedState.memoizedState!==c.memoizedState.memoizedState&&(t!==null?t.push(Oo):t=[Oo])}c=c.return}t!==null&&Lu(n,t,a,o),n.flags|=262144}function xl(t){for(t=t.firstContext;t!==null;){if(!jn(t.context._currentValue,t.memoizedValue))return!0;t=t.next}return!1}function Ss(t){xs=t,ta=null,t=t.dependencies,t!==null&&(t.firstContext=null)}function Rn(t){return Hp(xs,t)}function Sl(t,n){return xs===null&&Ss(t),Hp(t,n)}function Hp(t,n){var a=n._currentValue;if(n={context:n,memoizedValue:a,next:null},ta===null){if(t===null)throw Error(r(308));ta=n,t.dependencies={lanes:0,firstContext:n},t.flags|=524288}else ta=ta.next=n;return a}var Rx=typeof AbortController<"u"?AbortController:function(){var t=[],n=this.signal={aborted:!1,addEventListener:function(a,o){t.push(o)}};this.abort=function(){n.aborted=!0,t.forEach(function(a){return a()})}},Cx=s.unstable_scheduleCallback,wx=s.unstable_NormalPriority,pn={$$typeof:F,Consumer:null,Provider:null,_currentValue:null,_currentValue2:null,_threadCount:0};function Nu(){return{controller:new Rx,data:new Map,refCount:0}}function oo(t){t.refCount--,t.refCount===0&&Cx(wx,function(){t.controller.abort()})}var lo=null,Ou=0,tr=0,nr=null;function Dx(t,n){if(lo===null){var a=lo=[];Ou=0,tr=Bf(),nr={status:"pending",value:void 0,then:function(o){a.push(o)}}}return Ou++,n.then(Gp,Gp),n}function Gp(){if(--Ou===0&&lo!==null){nr!==null&&(nr.status="fulfilled");var t=lo;lo=null,tr=0,nr=null;for(var n=0;n<t.length;n++)(0,t[n])()}}function Ux(t,n){var a=[],o={status:"pending",value:null,reason:null,then:function(c){a.push(c)}};return t.then(function(){o.status="fulfilled",o.value=n;for(var c=0;c<a.length;c++)(0,a[c])(n)},function(c){for(o.status="rejected",o.reason=c,c=0;c<a.length;c++)(0,a[c])(void 0)}),o}var Vp=N.S;N.S=function(t,n){Tg=Ht(),typeof n=="object"&&n!==null&&typeof n.then=="function"&&Dx(t,n),Vp!==null&&Vp(t,n)};var Ms=xe(null);function Pu(){var t=Ms.current;return t!==null?t:Wt.pooledCache}function Ml(t,n){n===null?Te(Ms,Ms.current):Te(Ms,n.pool)}function kp(){var t=Pu();return t===null?null:{parent:pn._currentValue,pool:t}}var ir=Error(r(460)),zu=Error(r(474)),yl=Error(r(542)),El={then:function(){}};function Xp(t){return t=t.status,t==="fulfilled"||t==="rejected"}function Wp(t,n,a){switch(a=t[a],a===void 0?t.push(n):a!==n&&(n.then(Ji,Ji),n=a),n.status){case"fulfilled":return n.value;case"rejected":throw t=n.reason,Yp(t),t;default:if(typeof n.status=="string")n.then(Ji,Ji);else{if(t=Wt,t!==null&&100<t.shellSuspendCounter)throw Error(r(482));t=n,t.status="pending",t.then(function(o){if(n.status==="pending"){var c=n;c.status="fulfilled",c.value=o}},function(o){if(n.status==="pending"){var c=n;c.status="rejected",c.reason=o}})}switch(n.status){case"fulfilled":return n.value;case"rejected":throw t=n.reason,Yp(t),t}throw Es=n,ir}}function ys(t){try{var n=t._init;return n(t._payload)}catch(a){throw a!==null&&typeof a=="object"&&typeof a.then=="function"?(Es=a,ir):a}}var Es=null;function qp(){if(Es===null)throw Error(r(459));var t=Es;return Es=null,t}function Yp(t){if(t===ir||t===yl)throw Error(r(483))}var ar=null,co=0;function bl(t){var n=co;return co+=1,ar===null&&(ar=[]),Wp(ar,t,n)}function uo(t,n){n=n.props.ref,t.ref=n!==void 0?n:null}function Tl(t,n){throw n.$$typeof===g?Error(r(525)):(t=Object.prototype.toString.call(n),Error(r(31,t==="[object Object]"?"object with keys {"+Object.keys(n).join(", ")+"}":t)))}function Zp(t){function n(q,H){if(t){var Q=q.deletions;Q===null?(q.deletions=[H],q.flags|=16):Q.push(H)}}function a(q,H){if(!t)return null;for(;H!==null;)n(q,H),H=H.sibling;return null}function o(q){for(var H=new Map;q!==null;)q.key!==null?H.set(q.key,q):H.set(q.index,q),q=q.sibling;return H}function c(q,H){return q=ji(q,H),q.index=0,q.sibling=null,q}function f(q,H,Q){return q.index=Q,t?(Q=q.alternate,Q!==null?(Q=Q.index,Q<H?(q.flags|=67108866,H):Q):(q.flags|=67108866,H)):(q.flags|=1048576,H)}function _(q){return t&&q.alternate===null&&(q.flags|=67108866),q}function R(q,H,Q,ge){return H===null||H.tag!==6?(H=bu(Q,q.mode,ge),H.return=q,H):(H=c(H,Q),H.return=q,H)}function P(q,H,Q,ge){var Je=Q.type;return Je===D?fe(q,H,Q.props.children,ge,Q.key):H!==null&&(H.elementType===Je||typeof Je=="object"&&Je!==null&&Je.$$typeof===b&&ys(Je)===H.type)?(H=c(H,Q.props),uo(H,Q),H.return=q,H):(H=_l(Q.type,Q.key,Q.props,null,q.mode,ge),uo(H,Q),H.return=q,H)}function J(q,H,Q,ge){return H===null||H.tag!==4||H.stateNode.containerInfo!==Q.containerInfo||H.stateNode.implementation!==Q.implementation?(H=Tu(Q,q.mode,ge),H.return=q,H):(H=c(H,Q.children||[]),H.return=q,H)}function fe(q,H,Q,ge,Je){return H===null||H.tag!==7?(H=_s(Q,q.mode,ge,Je),H.return=q,H):(H=c(H,Q),H.return=q,H)}function _e(q,H,Q){if(typeof H=="string"&&H!==""||typeof H=="number"||typeof H=="bigint")return H=bu(""+H,q.mode,Q),H.return=q,H;if(typeof H=="object"&&H!==null){switch(H.$$typeof){case E:return Q=_l(H.type,H.key,H.props,null,q.mode,Q),uo(Q,H),Q.return=q,Q;case T:return H=Tu(H,q.mode,Q),H.return=q,H;case b:return H=ys(H),_e(q,H,Q)}if(K(H)||Z(H))return H=_s(H,q.mode,Q,null),H.return=q,H;if(typeof H.then=="function")return _e(q,bl(H),Q);if(H.$$typeof===F)return _e(q,Sl(q,H),Q);Tl(q,H)}return null}function ie(q,H,Q,ge){var Je=H!==null?H.key:null;if(typeof Q=="string"&&Q!==""||typeof Q=="number"||typeof Q=="bigint")return Je!==null?null:R(q,H,""+Q,ge);if(typeof Q=="object"&&Q!==null){switch(Q.$$typeof){case E:return Q.key===Je?P(q,H,Q,ge):null;case T:return Q.key===Je?J(q,H,Q,ge):null;case b:return Q=ys(Q),ie(q,H,Q,ge)}if(K(Q)||Z(Q))return Je!==null?null:fe(q,H,Q,ge,null);if(typeof Q.then=="function")return ie(q,H,bl(Q),ge);if(Q.$$typeof===F)return ie(q,H,Sl(q,Q),ge);Tl(q,Q)}return null}function re(q,H,Q,ge,Je){if(typeof ge=="string"&&ge!==""||typeof ge=="number"||typeof ge=="bigint")return q=q.get(Q)||null,R(H,q,""+ge,Je);if(typeof ge=="object"&&ge!==null){switch(ge.$$typeof){case E:return q=q.get(ge.key===null?Q:ge.key)||null,P(H,q,ge,Je);case T:return q=q.get(ge.key===null?Q:ge.key)||null,J(H,q,ge,Je);case b:return ge=ys(ge),re(q,H,Q,ge,Je)}if(K(ge)||Z(ge))return q=q.get(Q)||null,fe(H,q,ge,Je,null);if(typeof ge.then=="function")return re(q,H,Q,bl(ge),Je);if(ge.$$typeof===F)return re(q,H,Q,Sl(H,ge),Je);Tl(H,ge)}return null}function Ve(q,H,Q,ge){for(var Je=null,At=null,Xe=H,lt=H=0,_t=null;Xe!==null&&lt<Q.length;lt++){Xe.index>lt?(_t=Xe,Xe=null):_t=Xe.sibling;var Rt=ie(q,Xe,Q[lt],ge);if(Rt===null){Xe===null&&(Xe=_t);break}t&&Xe&&Rt.alternate===null&&n(q,Xe),H=f(Rt,H,lt),At===null?Je=Rt:At.sibling=Rt,At=Rt,Xe=_t}if(lt===Q.length)return a(q,Xe),Mt&&ea(q,lt),Je;if(Xe===null){for(;lt<Q.length;lt++)Xe=_e(q,Q[lt],ge),Xe!==null&&(H=f(Xe,H,lt),At===null?Je=Xe:At.sibling=Xe,At=Xe);return Mt&&ea(q,lt),Je}for(Xe=o(Xe);lt<Q.length;lt++)_t=re(Xe,q,lt,Q[lt],ge),_t!==null&&(t&&_t.alternate!==null&&Xe.delete(_t.key===null?lt:_t.key),H=f(_t,H,lt),At===null?Je=_t:At.sibling=_t,At=_t);return t&&Xe.forEach(function($a){return n(q,$a)}),Mt&&ea(q,lt),Je}function je(q,H,Q,ge){if(Q==null)throw Error(r(151));for(var Je=null,At=null,Xe=H,lt=H=0,_t=null,Rt=Q.next();Xe!==null&&!Rt.done;lt++,Rt=Q.next()){Xe.index>lt?(_t=Xe,Xe=null):_t=Xe.sibling;var $a=ie(q,Xe,Rt.value,ge);if($a===null){Xe===null&&(Xe=_t);break}t&&Xe&&$a.alternate===null&&n(q,Xe),H=f($a,H,lt),At===null?Je=$a:At.sibling=$a,At=$a,Xe=_t}if(Rt.done)return a(q,Xe),Mt&&ea(q,lt),Je;if(Xe===null){for(;!Rt.done;lt++,Rt=Q.next())Rt=_e(q,Rt.value,ge),Rt!==null&&(H=f(Rt,H,lt),At===null?Je=Rt:At.sibling=Rt,At=Rt);return Mt&&ea(q,lt),Je}for(Xe=o(Xe);!Rt.done;lt++,Rt=Q.next())Rt=re(Xe,q,lt,Rt.value,ge),Rt!==null&&(t&&Rt.alternate!==null&&Xe.delete(Rt.key===null?lt:Rt.key),H=f(Rt,H,lt),At===null?Je=Rt:At.sibling=Rt,At=Rt);return t&&Xe.forEach(function(VS){return n(q,VS)}),Mt&&ea(q,lt),Je}function kt(q,H,Q,ge){if(typeof Q=="object"&&Q!==null&&Q.type===D&&Q.key===null&&(Q=Q.props.children),typeof Q=="object"&&Q!==null){switch(Q.$$typeof){case E:e:{for(var Je=Q.key;H!==null;){if(H.key===Je){if(Je=Q.type,Je===D){if(H.tag===7){a(q,H.sibling),ge=c(H,Q.props.children),ge.return=q,q=ge;break e}}else if(H.elementType===Je||typeof Je=="object"&&Je!==null&&Je.$$typeof===b&&ys(Je)===H.type){a(q,H.sibling),ge=c(H,Q.props),uo(ge,Q),ge.return=q,q=ge;break e}a(q,H);break}else n(q,H);H=H.sibling}Q.type===D?(ge=_s(Q.props.children,q.mode,ge,Q.key),ge.return=q,q=ge):(ge=_l(Q.type,Q.key,Q.props,null,q.mode,ge),uo(ge,Q),ge.return=q,q=ge)}return _(q);case T:e:{for(Je=Q.key;H!==null;){if(H.key===Je)if(H.tag===4&&H.stateNode.containerInfo===Q.containerInfo&&H.stateNode.implementation===Q.implementation){a(q,H.sibling),ge=c(H,Q.children||[]),ge.return=q,q=ge;break e}else{a(q,H);break}else n(q,H);H=H.sibling}ge=Tu(Q,q.mode,ge),ge.return=q,q=ge}return _(q);case b:return Q=ys(Q),kt(q,H,Q,ge)}if(K(Q))return Ve(q,H,Q,ge);if(Z(Q)){if(Je=Z(Q),typeof Je!="function")throw Error(r(150));return Q=Je.call(Q),je(q,H,Q,ge)}if(typeof Q.then=="function")return kt(q,H,bl(Q),ge);if(Q.$$typeof===F)return kt(q,H,Sl(q,Q),ge);Tl(q,Q)}return typeof Q=="string"&&Q!==""||typeof Q=="number"||typeof Q=="bigint"?(Q=""+Q,H!==null&&H.tag===6?(a(q,H.sibling),ge=c(H,Q),ge.return=q,q=ge):(a(q,H),ge=bu(Q,q.mode,ge),ge.return=q,q=ge),_(q)):a(q,H)}return function(q,H,Q,ge){try{co=0;var Je=kt(q,H,Q,ge);return ar=null,Je}catch(Xe){if(Xe===ir||Xe===yl)throw Xe;var At=ei(29,Xe,null,q.mode);return At.lanes=ge,At.return=q,At}finally{}}}var bs=Zp(!0),Kp=Zp(!1),Pa=!1;function Fu(t){t.updateQueue={baseState:t.memoizedState,firstBaseUpdate:null,lastBaseUpdate:null,shared:{pending:null,lanes:0,hiddenCallbacks:null},callbacks:null}}function Bu(t,n){t=t.updateQueue,n.updateQueue===t&&(n.updateQueue={baseState:t.baseState,firstBaseUpdate:t.firstBaseUpdate,lastBaseUpdate:t.lastBaseUpdate,shared:t.shared,callbacks:null})}function za(t){return{lane:t,tag:0,payload:null,callback:null,next:null}}function Fa(t,n,a){var o=t.updateQueue;if(o===null)return null;if(o=o.shared,(Dt&2)!==0){var c=o.pending;return c===null?n.next=n:(n.next=c.next,c.next=n),o.pending=n,n=gl(t),Lp(t,null,a),n}return ml(t,o,n,a),gl(t)}function fo(t,n,a){if(n=n.updateQueue,n!==null&&(n=n.shared,(a&4194048)!==0)){var o=n.lanes;o&=t.pendingLanes,a|=o,n.lanes=a,Kn(t,a)}}function Iu(t,n){var a=t.updateQueue,o=t.alternate;if(o!==null&&(o=o.updateQueue,a===o)){var c=null,f=null;if(a=a.firstBaseUpdate,a!==null){do{var _={lane:a.lane,tag:a.tag,payload:a.payload,callback:null,next:null};f===null?c=f=_:f=f.next=_,a=a.next}while(a!==null);f===null?c=f=n:f=f.next=n}else c=f=n;a={baseState:o.baseState,firstBaseUpdate:c,lastBaseUpdate:f,shared:o.shared,callbacks:o.callbacks},t.updateQueue=a;return}t=a.lastBaseUpdate,t===null?a.firstBaseUpdate=n:t.next=n,a.lastBaseUpdate=n}var Hu=!1;function ho(){if(Hu){var t=nr;if(t!==null)throw t}}function po(t,n,a,o){Hu=!1;var c=t.updateQueue;Pa=!1;var f=c.firstBaseUpdate,_=c.lastBaseUpdate,R=c.shared.pending;if(R!==null){c.shared.pending=null;var P=R,J=P.next;P.next=null,_===null?f=J:_.next=J,_=P;var fe=t.alternate;fe!==null&&(fe=fe.updateQueue,R=fe.lastBaseUpdate,R!==_&&(R===null?fe.firstBaseUpdate=J:R.next=J,fe.lastBaseUpdate=P))}if(f!==null){var _e=c.baseState;_=0,fe=J=P=null,R=f;do{var ie=R.lane&-536870913,re=ie!==R.lane;if(re?(gt&ie)===ie:(o&ie)===ie){ie!==0&&ie===tr&&(Hu=!0),fe!==null&&(fe=fe.next={lane:0,tag:R.tag,payload:R.payload,callback:null,next:null});e:{var Ve=t,je=R;ie=n;var kt=a;switch(je.tag){case 1:if(Ve=je.payload,typeof Ve=="function"){_e=Ve.call(kt,_e,ie);break e}_e=Ve;break e;case 3:Ve.flags=Ve.flags&-65537|128;case 0:if(Ve=je.payload,ie=typeof Ve=="function"?Ve.call(kt,_e,ie):Ve,ie==null)break e;_e=x({},_e,ie);break e;case 2:Pa=!0}}ie=R.callback,ie!==null&&(t.flags|=64,re&&(t.flags|=8192),re=c.callbacks,re===null?c.callbacks=[ie]:re.push(ie))}else re={lane:ie,tag:R.tag,payload:R.payload,callback:R.callback,next:null},fe===null?(J=fe=re,P=_e):fe=fe.next=re,_|=ie;if(R=R.next,R===null){if(R=c.shared.pending,R===null)break;re=R,R=re.next,re.next=null,c.lastBaseUpdate=re,c.shared.pending=null}}while(!0);fe===null&&(P=_e),c.baseState=P,c.firstBaseUpdate=J,c.lastBaseUpdate=fe,f===null&&(c.shared.lanes=0),Va|=_,t.lanes=_,t.memoizedState=_e}}function Qp(t,n){if(typeof t!="function")throw Error(r(191,t));t.call(n)}function Jp(t,n){var a=t.callbacks;if(a!==null)for(t.callbacks=null,t=0;t<a.length;t++)Qp(a[t],n)}var sr=xe(null),Al=xe(0);function $p(t,n){t=fa,Te(Al,t),Te(sr,n),fa=t|n.baseLanes}function Gu(){Te(Al,fa),Te(sr,sr.current)}function Vu(){fa=Al.current,Se(sr),Se(Al)}var ti=xe(null),hi=null;function Ba(t){var n=t.alternate;Te(cn,cn.current&1),Te(ti,t),hi===null&&(n===null||sr.current!==null||n.memoizedState!==null)&&(hi=t)}function ku(t){Te(cn,cn.current),Te(ti,t),hi===null&&(hi=t)}function jp(t){t.tag===22?(Te(cn,cn.current),Te(ti,t),hi===null&&(hi=t)):Ia()}function Ia(){Te(cn,cn.current),Te(ti,ti.current)}function ni(t){Se(ti),hi===t&&(hi=null),Se(cn)}var cn=xe(0);function Rl(t){for(var n=t;n!==null;){if(n.tag===13){var a=n.memoizedState;if(a!==null&&(a=a.dehydrated,a===null||Qf(a)||Jf(a)))return n}else if(n.tag===19&&(n.memoizedProps.revealOrder==="forwards"||n.memoizedProps.revealOrder==="backwards"||n.memoizedProps.revealOrder==="unstable_legacy-backwards"||n.memoizedProps.revealOrder==="together")){if((n.flags&128)!==0)return n}else if(n.child!==null){n.child.return=n,n=n.child;continue}if(n===t)break;for(;n.sibling===null;){if(n.return===null||n.return===t)return null;n=n.return}n.sibling.return=n.return,n=n.sibling}return null}var ia=0,ot=null,Gt=null,mn=null,Cl=!1,rr=!1,Ts=!1,wl=0,mo=0,or=null,Lx=0;function sn(){throw Error(r(321))}function Xu(t,n){if(n===null)return!1;for(var a=0;a<n.length&&a<t.length;a++)if(!jn(t[a],n[a]))return!1;return!0}function Wu(t,n,a,o,c,f){return ia=f,ot=n,n.memoizedState=null,n.updateQueue=null,n.lanes=0,N.H=t===null||t.memoizedState===null?zm:of,Ts=!1,f=a(o,c),Ts=!1,rr&&(f=tm(n,a,o,c)),em(t),f}function em(t){N.H=vo;var n=Gt!==null&&Gt.next!==null;if(ia=0,mn=Gt=ot=null,Cl=!1,mo=0,or=null,n)throw Error(r(300));t===null||gn||(t=t.dependencies,t!==null&&xl(t)&&(gn=!0))}function tm(t,n,a,o){ot=t;var c=0;do{if(rr&&(or=null),mo=0,rr=!1,25<=c)throw Error(r(301));if(c+=1,mn=Gt=null,t.updateQueue!=null){var f=t.updateQueue;f.lastEffect=null,f.events=null,f.stores=null,f.memoCache!=null&&(f.memoCache.index=0)}N.H=Fm,f=n(a,o)}while(rr);return f}function Nx(){var t=N.H,n=t.useState()[0];return n=typeof n.then=="function"?go(n):n,t=t.useState()[0],(Gt!==null?Gt.memoizedState:null)!==t&&(ot.flags|=1024),n}function qu(){var t=wl!==0;return wl=0,t}function Yu(t,n,a){n.updateQueue=t.updateQueue,n.flags&=-2053,t.lanes&=~a}function Zu(t){if(Cl){for(t=t.memoizedState;t!==null;){var n=t.queue;n!==null&&(n.pending=null),t=t.next}Cl=!1}ia=0,mn=Gt=ot=null,rr=!1,mo=wl=0,or=null}function Pn(){var t={memoizedState:null,baseState:null,baseQueue:null,queue:null,next:null};return mn===null?ot.memoizedState=mn=t:mn=mn.next=t,mn}function un(){if(Gt===null){var t=ot.alternate;t=t!==null?t.memoizedState:null}else t=Gt.next;var n=mn===null?ot.memoizedState:mn.next;if(n!==null)mn=n,Gt=t;else{if(t===null)throw ot.alternate===null?Error(r(467)):Error(r(310));Gt=t,t={memoizedState:Gt.memoizedState,baseState:Gt.baseState,baseQueue:Gt.baseQueue,queue:Gt.queue,next:null},mn===null?ot.memoizedState=mn=t:mn=mn.next=t}return mn}function Dl(){return{lastEffect:null,events:null,stores:null,memoCache:null}}function go(t){var n=mo;return mo+=1,or===null&&(or=[]),t=Wp(or,t,n),n=ot,(mn===null?n.memoizedState:mn.next)===null&&(n=n.alternate,N.H=n===null||n.memoizedState===null?zm:of),t}function Ul(t){if(t!==null&&typeof t=="object"){if(typeof t.then=="function")return go(t);if(t.$$typeof===F)return Rn(t)}throw Error(r(438,String(t)))}function Ku(t){var n=null,a=ot.updateQueue;if(a!==null&&(n=a.memoCache),n==null){var o=ot.alternate;o!==null&&(o=o.updateQueue,o!==null&&(o=o.memoCache,o!=null&&(n={data:o.data.map(function(c){return c.slice()}),index:0})))}if(n==null&&(n={data:[],index:0}),a===null&&(a=Dl(),ot.updateQueue=a),a.memoCache=n,a=n.data[n.index],a===void 0)for(a=n.data[n.index]=Array(t),o=0;o<t;o++)a[o]=W;return n.index++,a}function aa(t,n){return typeof n=="function"?n(t):n}function Ll(t){var n=un();return Qu(n,Gt,t)}function Qu(t,n,a){var o=t.queue;if(o===null)throw Error(r(311));o.lastRenderedReducer=a;var c=t.baseQueue,f=o.pending;if(f!==null){if(c!==null){var _=c.next;c.next=f.next,f.next=_}n.baseQueue=c=f,o.pending=null}if(f=t.baseState,c===null)t.memoizedState=f;else{n=c.next;var R=_=null,P=null,J=n,fe=!1;do{var _e=J.lane&-536870913;if(_e!==J.lane?(gt&_e)===_e:(ia&_e)===_e){var ie=J.revertLane;if(ie===0)P!==null&&(P=P.next={lane:0,revertLane:0,gesture:null,action:J.action,hasEagerState:J.hasEagerState,eagerState:J.eagerState,next:null}),_e===tr&&(fe=!0);else if((ia&ie)===ie){J=J.next,ie===tr&&(fe=!0);continue}else _e={lane:0,revertLane:J.revertLane,gesture:null,action:J.action,hasEagerState:J.hasEagerState,eagerState:J.eagerState,next:null},P===null?(R=P=_e,_=f):P=P.next=_e,ot.lanes|=ie,Va|=ie;_e=J.action,Ts&&a(f,_e),f=J.hasEagerState?J.eagerState:a(f,_e)}else ie={lane:_e,revertLane:J.revertLane,gesture:J.gesture,action:J.action,hasEagerState:J.hasEagerState,eagerState:J.eagerState,next:null},P===null?(R=P=ie,_=f):P=P.next=ie,ot.lanes|=_e,Va|=_e;J=J.next}while(J!==null&&J!==n);if(P===null?_=f:P.next=R,!jn(f,t.memoizedState)&&(gn=!0,fe&&(a=nr,a!==null)))throw a;t.memoizedState=f,t.baseState=_,t.baseQueue=P,o.lastRenderedState=f}return c===null&&(o.lanes=0),[t.memoizedState,o.dispatch]}function Ju(t){var n=un(),a=n.queue;if(a===null)throw Error(r(311));a.lastRenderedReducer=t;var o=a.dispatch,c=a.pending,f=n.memoizedState;if(c!==null){a.pending=null;var _=c=c.next;do f=t(f,_.action),_=_.next;while(_!==c);jn(f,n.memoizedState)||(gn=!0),n.memoizedState=f,n.baseQueue===null&&(n.baseState=f),a.lastRenderedState=f}return[f,o]}function nm(t,n,a){var o=ot,c=un(),f=Mt;if(f){if(a===void 0)throw Error(r(407));a=a()}else a=n();var _=!jn((Gt||c).memoizedState,a);if(_&&(c.memoizedState=a,gn=!0),c=c.queue,ef(sm.bind(null,o,c,t),[t]),c.getSnapshot!==n||_||mn!==null&&mn.memoizedState.tag&1){if(o.flags|=2048,lr(9,{destroy:void 0},am.bind(null,o,c,a,n),null),Wt===null)throw Error(r(349));f||(ia&127)!==0||im(o,n,a)}return a}function im(t,n,a){t.flags|=16384,t={getSnapshot:n,value:a},n=ot.updateQueue,n===null?(n=Dl(),ot.updateQueue=n,n.stores=[t]):(a=n.stores,a===null?n.stores=[t]:a.push(t))}function am(t,n,a,o){n.value=a,n.getSnapshot=o,rm(n)&&om(t)}function sm(t,n,a){return a(function(){rm(n)&&om(t)})}function rm(t){var n=t.getSnapshot;t=t.value;try{var a=n();return!jn(t,a)}catch{return!0}}function om(t){var n=gs(t,2);n!==null&&qn(n,t,2)}function $u(t){var n=Pn();if(typeof t=="function"){var a=t;if(t=a(),Ts){be(!0);try{a()}finally{be(!1)}}}return n.memoizedState=n.baseState=t,n.queue={pending:null,lanes:0,dispatch:null,lastRenderedReducer:aa,lastRenderedState:t},n}function lm(t,n,a,o){return t.baseState=a,Qu(t,Gt,typeof o=="function"?o:aa)}function Ox(t,n,a,o,c){if(Pl(t))throw Error(r(485));if(t=n.action,t!==null){var f={payload:c,action:t,next:null,isTransition:!0,status:"pending",value:null,reason:null,listeners:[],then:function(_){f.listeners.push(_)}};N.T!==null?a(!0):f.isTransition=!1,o(f),a=n.pending,a===null?(f.next=n.pending=f,cm(n,f)):(f.next=a.next,n.pending=a.next=f)}}function cm(t,n){var a=n.action,o=n.payload,c=t.state;if(n.isTransition){var f=N.T,_={};N.T=_;try{var R=a(c,o),P=N.S;P!==null&&P(_,R),um(t,n,R)}catch(J){ju(t,n,J)}finally{f!==null&&_.types!==null&&(f.types=_.types),N.T=f}}else try{f=a(c,o),um(t,n,f)}catch(J){ju(t,n,J)}}function um(t,n,a){a!==null&&typeof a=="object"&&typeof a.then=="function"?a.then(function(o){fm(t,n,o)},function(o){return ju(t,n,o)}):fm(t,n,a)}function fm(t,n,a){n.status="fulfilled",n.value=a,dm(n),t.state=a,n=t.pending,n!==null&&(a=n.next,a===n?t.pending=null:(a=a.next,n.next=a,cm(t,a)))}function ju(t,n,a){var o=t.pending;if(t.pending=null,o!==null){o=o.next;do n.status="rejected",n.reason=a,dm(n),n=n.next;while(n!==o)}t.action=null}function dm(t){t=t.listeners;for(var n=0;n<t.length;n++)(0,t[n])()}function hm(t,n){return n}function pm(t,n){if(Mt){var a=Wt.formState;if(a!==null){e:{var o=ot;if(Mt){if(Zt){t:{for(var c=Zt,f=di;c.nodeType!==8;){if(!f){c=null;break t}if(c=pi(c.nextSibling),c===null){c=null;break t}}f=c.data,c=f==="F!"||f==="F"?c:null}if(c){Zt=pi(c.nextSibling),o=c.data==="F!";break e}}Na(o)}o=!1}o&&(n=a[0])}}return a=Pn(),a.memoizedState=a.baseState=n,o={pending:null,lanes:0,dispatch:null,lastRenderedReducer:hm,lastRenderedState:n},a.queue=o,a=Nm.bind(null,ot,o),o.dispatch=a,o=$u(!1),f=rf.bind(null,ot,!1,o.queue),o=Pn(),c={state:n,dispatch:null,action:t,pending:null},o.queue=c,a=Ox.bind(null,ot,c,f,a),c.dispatch=a,o.memoizedState=t,[n,a,!1]}function mm(t){var n=un();return gm(n,Gt,t)}function gm(t,n,a){if(n=Qu(t,n,hm)[0],t=Ll(aa)[0],typeof n=="object"&&n!==null&&typeof n.then=="function")try{var o=go(n)}catch(_){throw _===ir?yl:_}else o=n;n=un();var c=n.queue,f=c.dispatch;return a!==n.memoizedState&&(ot.flags|=2048,lr(9,{destroy:void 0},Px.bind(null,c,a),null)),[o,f,t]}function Px(t,n){t.action=n}function _m(t){var n=un(),a=Gt;if(a!==null)return gm(n,a,t);un(),n=n.memoizedState,a=un();var o=a.queue.dispatch;return a.memoizedState=t,[n,o,!1]}function lr(t,n,a,o){return t={tag:t,create:a,deps:o,inst:n,next:null},n=ot.updateQueue,n===null&&(n=Dl(),ot.updateQueue=n),a=n.lastEffect,a===null?n.lastEffect=t.next=t:(o=a.next,a.next=t,t.next=o,n.lastEffect=t),t}function vm(){return un().memoizedState}function Nl(t,n,a,o){var c=Pn();ot.flags|=t,c.memoizedState=lr(1|n,{destroy:void 0},a,o===void 0?null:o)}function Ol(t,n,a,o){var c=un();o=o===void 0?null:o;var f=c.memoizedState.inst;Gt!==null&&o!==null&&Xu(o,Gt.memoizedState.deps)?c.memoizedState=lr(n,f,a,o):(ot.flags|=t,c.memoizedState=lr(1|n,f,a,o))}function xm(t,n){Nl(8390656,8,t,n)}function ef(t,n){Ol(2048,8,t,n)}function zx(t){ot.flags|=4;var n=ot.updateQueue;if(n===null)n=Dl(),ot.updateQueue=n,n.events=[t];else{var a=n.events;a===null?n.events=[t]:a.push(t)}}function Sm(t){var n=un().memoizedState;return zx({ref:n,nextImpl:t}),function(){if((Dt&2)!==0)throw Error(r(440));return n.impl.apply(void 0,arguments)}}function Mm(t,n){return Ol(4,2,t,n)}function ym(t,n){return Ol(4,4,t,n)}function Em(t,n){if(typeof n=="function"){t=t();var a=n(t);return function(){typeof a=="function"?a():n(null)}}if(n!=null)return t=t(),n.current=t,function(){n.current=null}}function bm(t,n,a){a=a!=null?a.concat([t]):null,Ol(4,4,Em.bind(null,n,t),a)}function tf(){}function Tm(t,n){var a=un();n=n===void 0?null:n;var o=a.memoizedState;return n!==null&&Xu(n,o[1])?o[0]:(a.memoizedState=[t,n],t)}function Am(t,n){var a=un();n=n===void 0?null:n;var o=a.memoizedState;if(n!==null&&Xu(n,o[1]))return o[0];if(o=t(),Ts){be(!0);try{t()}finally{be(!1)}}return a.memoizedState=[o,n],o}function nf(t,n,a){return a===void 0||(ia&1073741824)!==0&&(gt&261930)===0?t.memoizedState=n:(t.memoizedState=a,t=Rg(),ot.lanes|=t,Va|=t,a)}function Rm(t,n,a,o){return jn(a,n)?a:sr.current!==null?(t=nf(t,a,o),jn(t,n)||(gn=!0),t):(ia&42)===0||(ia&1073741824)!==0&&(gt&261930)===0?(gn=!0,t.memoizedState=a):(t=Rg(),ot.lanes|=t,Va|=t,n)}function Cm(t,n,a,o,c){var f=I.p;I.p=f!==0&&8>f?f:8;var _=N.T,R={};N.T=R,rf(t,!1,n,a);try{var P=c(),J=N.S;if(J!==null&&J(R,P),P!==null&&typeof P=="object"&&typeof P.then=="function"){var fe=Ux(P,o);_o(t,n,fe,si(t))}else _o(t,n,o,si(t))}catch(_e){_o(t,n,{then:function(){},status:"rejected",reason:_e},si())}finally{I.p=f,_!==null&&R.types!==null&&(_.types=R.types),N.T=_}}function Fx(){}function af(t,n,a,o){if(t.tag!==5)throw Error(r(476));var c=wm(t).queue;Cm(t,c,n,$,a===null?Fx:function(){return Dm(t),a(o)})}function wm(t){var n=t.memoizedState;if(n!==null)return n;n={memoizedState:$,baseState:$,baseQueue:null,queue:{pending:null,lanes:0,dispatch:null,lastRenderedReducer:aa,lastRenderedState:$},next:null};var a={};return n.next={memoizedState:a,baseState:a,baseQueue:null,queue:{pending:null,lanes:0,dispatch:null,lastRenderedReducer:aa,lastRenderedState:a},next:null},t.memoizedState=n,t=t.alternate,t!==null&&(t.memoizedState=n),n}function Dm(t){var n=wm(t);n.next===null&&(n=t.alternate.memoizedState),_o(t,n.next.queue,{},si())}function sf(){return Rn(Oo)}function Um(){return un().memoizedState}function Lm(){return un().memoizedState}function Bx(t){for(var n=t.return;n!==null;){switch(n.tag){case 24:case 3:var a=si();t=za(a);var o=Fa(n,t,a);o!==null&&(qn(o,n,a),fo(o,n,a)),n={cache:Nu()},t.payload=n;return}n=n.return}}function Ix(t,n,a){var o=si();a={lane:o,revertLane:0,gesture:null,action:a,hasEagerState:!1,eagerState:null,next:null},Pl(t)?Om(n,a):(a=yu(t,n,a,o),a!==null&&(qn(a,t,o),Pm(a,n,o)))}function Nm(t,n,a){var o=si();_o(t,n,a,o)}function _o(t,n,a,o){var c={lane:o,revertLane:0,gesture:null,action:a,hasEagerState:!1,eagerState:null,next:null};if(Pl(t))Om(n,c);else{var f=t.alternate;if(t.lanes===0&&(f===null||f.lanes===0)&&(f=n.lastRenderedReducer,f!==null))try{var _=n.lastRenderedState,R=f(_,a);if(c.hasEagerState=!0,c.eagerState=R,jn(R,_))return ml(t,n,c,0),Wt===null&&pl(),!1}catch{}finally{}if(a=yu(t,n,c,o),a!==null)return qn(a,t,o),Pm(a,n,o),!0}return!1}function rf(t,n,a,o){if(o={lane:2,revertLane:Bf(),gesture:null,action:o,hasEagerState:!1,eagerState:null,next:null},Pl(t)){if(n)throw Error(r(479))}else n=yu(t,a,o,2),n!==null&&qn(n,t,2)}function Pl(t){var n=t.alternate;return t===ot||n!==null&&n===ot}function Om(t,n){rr=Cl=!0;var a=t.pending;a===null?n.next=n:(n.next=a.next,a.next=n),t.pending=n}function Pm(t,n,a){if((a&4194048)!==0){var o=n.lanes;o&=t.pendingLanes,a|=o,n.lanes=a,Kn(t,a)}}var vo={readContext:Rn,use:Ul,useCallback:sn,useContext:sn,useEffect:sn,useImperativeHandle:sn,useLayoutEffect:sn,useInsertionEffect:sn,useMemo:sn,useReducer:sn,useRef:sn,useState:sn,useDebugValue:sn,useDeferredValue:sn,useTransition:sn,useSyncExternalStore:sn,useId:sn,useHostTransitionStatus:sn,useFormState:sn,useActionState:sn,useOptimistic:sn,useMemoCache:sn,useCacheRefresh:sn};vo.useEffectEvent=sn;var zm={readContext:Rn,use:Ul,useCallback:function(t,n){return Pn().memoizedState=[t,n===void 0?null:n],t},useContext:Rn,useEffect:xm,useImperativeHandle:function(t,n,a){a=a!=null?a.concat([t]):null,Nl(4194308,4,Em.bind(null,n,t),a)},useLayoutEffect:function(t,n){return Nl(4194308,4,t,n)},useInsertionEffect:function(t,n){Nl(4,2,t,n)},useMemo:function(t,n){var a=Pn();n=n===void 0?null:n;var o=t();if(Ts){be(!0);try{t()}finally{be(!1)}}return a.memoizedState=[o,n],o},useReducer:function(t,n,a){var o=Pn();if(a!==void 0){var c=a(n);if(Ts){be(!0);try{a(n)}finally{be(!1)}}}else c=n;return o.memoizedState=o.baseState=c,t={pending:null,lanes:0,dispatch:null,lastRenderedReducer:t,lastRenderedState:c},o.queue=t,t=t.dispatch=Ix.bind(null,ot,t),[o.memoizedState,t]},useRef:function(t){var n=Pn();return t={current:t},n.memoizedState=t},useState:function(t){t=$u(t);var n=t.queue,a=Nm.bind(null,ot,n);return n.dispatch=a,[t.memoizedState,a]},useDebugValue:tf,useDeferredValue:function(t,n){var a=Pn();return nf(a,t,n)},useTransition:function(){var t=$u(!1);return t=Cm.bind(null,ot,t.queue,!0,!1),Pn().memoizedState=t,[!1,t]},useSyncExternalStore:function(t,n,a){var o=ot,c=Pn();if(Mt){if(a===void 0)throw Error(r(407));a=a()}else{if(a=n(),Wt===null)throw Error(r(349));(gt&127)!==0||im(o,n,a)}c.memoizedState=a;var f={value:a,getSnapshot:n};return c.queue=f,xm(sm.bind(null,o,f,t),[t]),o.flags|=2048,lr(9,{destroy:void 0},am.bind(null,o,f,a,n),null),a},useId:function(){var t=Pn(),n=Wt.identifierPrefix;if(Mt){var a=zi,o=Pi;a=(o&~(1<<32-Pe(o)-1)).toString(32)+a,n="_"+n+"R_"+a,a=wl++,0<a&&(n+="H"+a.toString(32)),n+="_"}else a=Lx++,n="_"+n+"r_"+a.toString(32)+"_";return t.memoizedState=n},useHostTransitionStatus:sf,useFormState:pm,useActionState:pm,useOptimistic:function(t){var n=Pn();n.memoizedState=n.baseState=t;var a={pending:null,lanes:0,dispatch:null,lastRenderedReducer:null,lastRenderedState:null};return n.queue=a,n=rf.bind(null,ot,!0,a),a.dispatch=n,[t,n]},useMemoCache:Ku,useCacheRefresh:function(){return Pn().memoizedState=Bx.bind(null,ot)},useEffectEvent:function(t){var n=Pn(),a={impl:t};return n.memoizedState=a,function(){if((Dt&2)!==0)throw Error(r(440));return a.impl.apply(void 0,arguments)}}},of={readContext:Rn,use:Ul,useCallback:Tm,useContext:Rn,useEffect:ef,useImperativeHandle:bm,useInsertionEffect:Mm,useLayoutEffect:ym,useMemo:Am,useReducer:Ll,useRef:vm,useState:function(){return Ll(aa)},useDebugValue:tf,useDeferredValue:function(t,n){var a=un();return Rm(a,Gt.memoizedState,t,n)},useTransition:function(){var t=Ll(aa)[0],n=un().memoizedState;return[typeof t=="boolean"?t:go(t),n]},useSyncExternalStore:nm,useId:Um,useHostTransitionStatus:sf,useFormState:mm,useActionState:mm,useOptimistic:function(t,n){var a=un();return lm(a,Gt,t,n)},useMemoCache:Ku,useCacheRefresh:Lm};of.useEffectEvent=Sm;var Fm={readContext:Rn,use:Ul,useCallback:Tm,useContext:Rn,useEffect:ef,useImperativeHandle:bm,useInsertionEffect:Mm,useLayoutEffect:ym,useMemo:Am,useReducer:Ju,useRef:vm,useState:function(){return Ju(aa)},useDebugValue:tf,useDeferredValue:function(t,n){var a=un();return Gt===null?nf(a,t,n):Rm(a,Gt.memoizedState,t,n)},useTransition:function(){var t=Ju(aa)[0],n=un().memoizedState;return[typeof t=="boolean"?t:go(t),n]},useSyncExternalStore:nm,useId:Um,useHostTransitionStatus:sf,useFormState:_m,useActionState:_m,useOptimistic:function(t,n){var a=un();return Gt!==null?lm(a,Gt,t,n):(a.baseState=t,[t,a.queue.dispatch])},useMemoCache:Ku,useCacheRefresh:Lm};Fm.useEffectEvent=Sm;function lf(t,n,a,o){n=t.memoizedState,a=a(o,n),a=a==null?n:x({},n,a),t.memoizedState=a,t.lanes===0&&(t.updateQueue.baseState=a)}var cf={enqueueSetState:function(t,n,a){t=t._reactInternals;var o=si(),c=za(o);c.payload=n,a!=null&&(c.callback=a),n=Fa(t,c,o),n!==null&&(qn(n,t,o),fo(n,t,o))},enqueueReplaceState:function(t,n,a){t=t._reactInternals;var o=si(),c=za(o);c.tag=1,c.payload=n,a!=null&&(c.callback=a),n=Fa(t,c,o),n!==null&&(qn(n,t,o),fo(n,t,o))},enqueueForceUpdate:function(t,n){t=t._reactInternals;var a=si(),o=za(a);o.tag=2,n!=null&&(o.callback=n),n=Fa(t,o,a),n!==null&&(qn(n,t,a),fo(n,t,a))}};function Bm(t,n,a,o,c,f,_){return t=t.stateNode,typeof t.shouldComponentUpdate=="function"?t.shouldComponentUpdate(o,f,_):n.prototype&&n.prototype.isPureReactComponent?!io(a,o)||!io(c,f):!0}function Im(t,n,a,o){t=n.state,typeof n.componentWillReceiveProps=="function"&&n.componentWillReceiveProps(a,o),typeof n.UNSAFE_componentWillReceiveProps=="function"&&n.UNSAFE_componentWillReceiveProps(a,o),n.state!==t&&cf.enqueueReplaceState(n,n.state,null)}function As(t,n){var a=n;if("ref"in n){a={};for(var o in n)o!=="ref"&&(a[o]=n[o])}if(t=t.defaultProps){a===n&&(a=x({},a));for(var c in t)a[c]===void 0&&(a[c]=t[c])}return a}function Hm(t){hl(t)}function Gm(t){console.error(t)}function Vm(t){hl(t)}function zl(t,n){try{var a=t.onUncaughtError;a(n.value,{componentStack:n.stack})}catch(o){setTimeout(function(){throw o})}}function km(t,n,a){try{var o=t.onCaughtError;o(a.value,{componentStack:a.stack,errorBoundary:n.tag===1?n.stateNode:null})}catch(c){setTimeout(function(){throw c})}}function uf(t,n,a){return a=za(a),a.tag=3,a.payload={element:null},a.callback=function(){zl(t,n)},a}function Xm(t){return t=za(t),t.tag=3,t}function Wm(t,n,a,o){var c=a.type.getDerivedStateFromError;if(typeof c=="function"){var f=o.value;t.payload=function(){return c(f)},t.callback=function(){km(n,a,o)}}var _=a.stateNode;_!==null&&typeof _.componentDidCatch=="function"&&(t.callback=function(){km(n,a,o),typeof c!="function"&&(ka===null?ka=new Set([this]):ka.add(this));var R=o.stack;this.componentDidCatch(o.value,{componentStack:R!==null?R:""})})}function Hx(t,n,a,o,c){if(a.flags|=32768,o!==null&&typeof o=="object"&&typeof o.then=="function"){if(n=a.alternate,n!==null&&er(n,a,c,!0),a=ti.current,a!==null){switch(a.tag){case 31:case 13:return hi===null?Zl():a.alternate===null&&rn===0&&(rn=3),a.flags&=-257,a.flags|=65536,a.lanes=c,o===El?a.flags|=16384:(n=a.updateQueue,n===null?a.updateQueue=new Set([o]):n.add(o),Pf(t,o,c)),!1;case 22:return a.flags|=65536,o===El?a.flags|=16384:(n=a.updateQueue,n===null?(n={transitions:null,markerInstances:null,retryQueue:new Set([o])},a.updateQueue=n):(a=n.retryQueue,a===null?n.retryQueue=new Set([o]):a.add(o)),Pf(t,o,c)),!1}throw Error(r(435,a.tag))}return Pf(t,o,c),Zl(),!1}if(Mt)return n=ti.current,n!==null?((n.flags&65536)===0&&(n.flags|=256),n.flags|=65536,n.lanes=c,o!==Cu&&(t=Error(r(422),{cause:o}),ro(ci(t,a)))):(o!==Cu&&(n=Error(r(423),{cause:o}),ro(ci(n,a))),t=t.current.alternate,t.flags|=65536,c&=-c,t.lanes|=c,o=ci(o,a),c=uf(t.stateNode,o,c),Iu(t,c),rn!==4&&(rn=2)),!1;var f=Error(r(520),{cause:o});if(f=ci(f,a),Ao===null?Ao=[f]:Ao.push(f),rn!==4&&(rn=2),n===null)return!0;o=ci(o,a),a=n;do{switch(a.tag){case 3:return a.flags|=65536,t=c&-c,a.lanes|=t,t=uf(a.stateNode,o,t),Iu(a,t),!1;case 1:if(n=a.type,f=a.stateNode,(a.flags&128)===0&&(typeof n.getDerivedStateFromError=="function"||f!==null&&typeof f.componentDidCatch=="function"&&(ka===null||!ka.has(f))))return a.flags|=65536,c&=-c,a.lanes|=c,c=Xm(c),Wm(c,t,a,o),Iu(a,c),!1}a=a.return}while(a!==null);return!1}var ff=Error(r(461)),gn=!1;function Cn(t,n,a,o){n.child=t===null?Kp(n,null,a,o):bs(n,t.child,a,o)}function qm(t,n,a,o,c){a=a.render;var f=n.ref;if("ref"in o){var _={};for(var R in o)R!=="ref"&&(_[R]=o[R])}else _=o;return Ss(n),o=Wu(t,n,a,_,f,c),R=qu(),t!==null&&!gn?(Yu(t,n,c),sa(t,n,c)):(Mt&&R&&Au(n),n.flags|=1,Cn(t,n,o,c),n.child)}function Ym(t,n,a,o,c){if(t===null){var f=a.type;return typeof f=="function"&&!Eu(f)&&f.defaultProps===void 0&&a.compare===null?(n.tag=15,n.type=f,Zm(t,n,f,o,c)):(t=_l(a.type,null,o,n,n.mode,c),t.ref=n.ref,t.return=n,n.child=t)}if(f=t.child,!xf(t,c)){var _=f.memoizedProps;if(a=a.compare,a=a!==null?a:io,a(_,o)&&t.ref===n.ref)return sa(t,n,c)}return n.flags|=1,t=ji(f,o),t.ref=n.ref,t.return=n,n.child=t}function Zm(t,n,a,o,c){if(t!==null){var f=t.memoizedProps;if(io(f,o)&&t.ref===n.ref)if(gn=!1,n.pendingProps=o=f,xf(t,c))(t.flags&131072)!==0&&(gn=!0);else return n.lanes=t.lanes,sa(t,n,c)}return df(t,n,a,o,c)}function Km(t,n,a,o){var c=o.children,f=t!==null?t.memoizedState:null;if(t===null&&n.stateNode===null&&(n.stateNode={_visibility:1,_pendingMarkers:null,_retryCache:null,_transitions:null}),o.mode==="hidden"){if((n.flags&128)!==0){if(f=f!==null?f.baseLanes|a:a,t!==null){for(o=n.child=t.child,c=0;o!==null;)c=c|o.lanes|o.childLanes,o=o.sibling;o=c&~f}else o=0,n.child=null;return Qm(t,n,f,a,o)}if((a&536870912)!==0)n.memoizedState={baseLanes:0,cachePool:null},t!==null&&Ml(n,f!==null?f.cachePool:null),f!==null?$p(n,f):Gu(),jp(n);else return o=n.lanes=536870912,Qm(t,n,f!==null?f.baseLanes|a:a,a,o)}else f!==null?(Ml(n,f.cachePool),$p(n,f),Ia(),n.memoizedState=null):(t!==null&&Ml(n,null),Gu(),Ia());return Cn(t,n,c,a),n.child}function xo(t,n){return t!==null&&t.tag===22||n.stateNode!==null||(n.stateNode={_visibility:1,_pendingMarkers:null,_retryCache:null,_transitions:null}),n.sibling}function Qm(t,n,a,o,c){var f=Pu();return f=f===null?null:{parent:pn._currentValue,pool:f},n.memoizedState={baseLanes:a,cachePool:f},t!==null&&Ml(n,null),Gu(),jp(n),t!==null&&er(t,n,o,!0),n.childLanes=c,null}function Fl(t,n){return n=Il({mode:n.mode,children:n.children},t.mode),n.ref=t.ref,t.child=n,n.return=t,n}function Jm(t,n,a){return bs(n,t.child,null,a),t=Fl(n,n.pendingProps),t.flags|=2,ni(n),n.memoizedState=null,t}function Gx(t,n,a){var o=n.pendingProps,c=(n.flags&128)!==0;if(n.flags&=-129,t===null){if(Mt){if(o.mode==="hidden")return t=Fl(n,o),n.lanes=536870912,xo(null,t);if(ku(n),(t=Zt)?(t=c0(t,di),t=t!==null&&t.data==="&"?t:null,t!==null&&(n.memoizedState={dehydrated:t,treeContext:Ua!==null?{id:Pi,overflow:zi}:null,retryLane:536870912,hydrationErrors:null},a=Op(t),a.return=n,n.child=a,An=n,Zt=null)):t=null,t===null)throw Na(n);return n.lanes=536870912,null}return Fl(n,o)}var f=t.memoizedState;if(f!==null){var _=f.dehydrated;if(ku(n),c)if(n.flags&256)n.flags&=-257,n=Jm(t,n,a);else if(n.memoizedState!==null)n.child=t.child,n.flags|=128,n=null;else throw Error(r(558));else if(gn||er(t,n,a,!1),c=(a&t.childLanes)!==0,gn||c){if(o=Wt,o!==null&&(_=Qn(o,a),_!==0&&_!==f.retryLane))throw f.retryLane=_,gs(t,_),qn(o,t,_),ff;Zl(),n=Jm(t,n,a)}else t=f.treeContext,Zt=pi(_.nextSibling),An=n,Mt=!0,La=null,di=!1,t!==null&&Fp(n,t),n=Fl(n,o),n.flags|=4096;return n}return t=ji(t.child,{mode:o.mode,children:o.children}),t.ref=n.ref,n.child=t,t.return=n,t}function Bl(t,n){var a=n.ref;if(a===null)t!==null&&t.ref!==null&&(n.flags|=4194816);else{if(typeof a!="function"&&typeof a!="object")throw Error(r(284));(t===null||t.ref!==a)&&(n.flags|=4194816)}}function df(t,n,a,o,c){return Ss(n),a=Wu(t,n,a,o,void 0,c),o=qu(),t!==null&&!gn?(Yu(t,n,c),sa(t,n,c)):(Mt&&o&&Au(n),n.flags|=1,Cn(t,n,a,c),n.child)}function $m(t,n,a,o,c,f){return Ss(n),n.updateQueue=null,a=tm(n,o,a,c),em(t),o=qu(),t!==null&&!gn?(Yu(t,n,f),sa(t,n,f)):(Mt&&o&&Au(n),n.flags|=1,Cn(t,n,a,f),n.child)}function jm(t,n,a,o,c){if(Ss(n),n.stateNode===null){var f=Qs,_=a.contextType;typeof _=="object"&&_!==null&&(f=Rn(_)),f=new a(o,f),n.memoizedState=f.state!==null&&f.state!==void 0?f.state:null,f.updater=cf,n.stateNode=f,f._reactInternals=n,f=n.stateNode,f.props=o,f.state=n.memoizedState,f.refs={},Fu(n),_=a.contextType,f.context=typeof _=="object"&&_!==null?Rn(_):Qs,f.state=n.memoizedState,_=a.getDerivedStateFromProps,typeof _=="function"&&(lf(n,a,_,o),f.state=n.memoizedState),typeof a.getDerivedStateFromProps=="function"||typeof f.getSnapshotBeforeUpdate=="function"||typeof f.UNSAFE_componentWillMount!="function"&&typeof f.componentWillMount!="function"||(_=f.state,typeof f.componentWillMount=="function"&&f.componentWillMount(),typeof f.UNSAFE_componentWillMount=="function"&&f.UNSAFE_componentWillMount(),_!==f.state&&cf.enqueueReplaceState(f,f.state,null),po(n,o,f,c),ho(),f.state=n.memoizedState),typeof f.componentDidMount=="function"&&(n.flags|=4194308),o=!0}else if(t===null){f=n.stateNode;var R=n.memoizedProps,P=As(a,R);f.props=P;var J=f.context,fe=a.contextType;_=Qs,typeof fe=="object"&&fe!==null&&(_=Rn(fe));var _e=a.getDerivedStateFromProps;fe=typeof _e=="function"||typeof f.getSnapshotBeforeUpdate=="function",R=n.pendingProps!==R,fe||typeof f.UNSAFE_componentWillReceiveProps!="function"&&typeof f.componentWillReceiveProps!="function"||(R||J!==_)&&Im(n,f,o,_),Pa=!1;var ie=n.memoizedState;f.state=ie,po(n,o,f,c),ho(),J=n.memoizedState,R||ie!==J||Pa?(typeof _e=="function"&&(lf(n,a,_e,o),J=n.memoizedState),(P=Pa||Bm(n,a,P,o,ie,J,_))?(fe||typeof f.UNSAFE_componentWillMount!="function"&&typeof f.componentWillMount!="function"||(typeof f.componentWillMount=="function"&&f.componentWillMount(),typeof f.UNSAFE_componentWillMount=="function"&&f.UNSAFE_componentWillMount()),typeof f.componentDidMount=="function"&&(n.flags|=4194308)):(typeof f.componentDidMount=="function"&&(n.flags|=4194308),n.memoizedProps=o,n.memoizedState=J),f.props=o,f.state=J,f.context=_,o=P):(typeof f.componentDidMount=="function"&&(n.flags|=4194308),o=!1)}else{f=n.stateNode,Bu(t,n),_=n.memoizedProps,fe=As(a,_),f.props=fe,_e=n.pendingProps,ie=f.context,J=a.contextType,P=Qs,typeof J=="object"&&J!==null&&(P=Rn(J)),R=a.getDerivedStateFromProps,(J=typeof R=="function"||typeof f.getSnapshotBeforeUpdate=="function")||typeof f.UNSAFE_componentWillReceiveProps!="function"&&typeof f.componentWillReceiveProps!="function"||(_!==_e||ie!==P)&&Im(n,f,o,P),Pa=!1,ie=n.memoizedState,f.state=ie,po(n,o,f,c),ho();var re=n.memoizedState;_!==_e||ie!==re||Pa||t!==null&&t.dependencies!==null&&xl(t.dependencies)?(typeof R=="function"&&(lf(n,a,R,o),re=n.memoizedState),(fe=Pa||Bm(n,a,fe,o,ie,re,P)||t!==null&&t.dependencies!==null&&xl(t.dependencies))?(J||typeof f.UNSAFE_componentWillUpdate!="function"&&typeof f.componentWillUpdate!="function"||(typeof f.componentWillUpdate=="function"&&f.componentWillUpdate(o,re,P),typeof f.UNSAFE_componentWillUpdate=="function"&&f.UNSAFE_componentWillUpdate(o,re,P)),typeof f.componentDidUpdate=="function"&&(n.flags|=4),typeof f.getSnapshotBeforeUpdate=="function"&&(n.flags|=1024)):(typeof f.componentDidUpdate!="function"||_===t.memoizedProps&&ie===t.memoizedState||(n.flags|=4),typeof f.getSnapshotBeforeUpdate!="function"||_===t.memoizedProps&&ie===t.memoizedState||(n.flags|=1024),n.memoizedProps=o,n.memoizedState=re),f.props=o,f.state=re,f.context=P,o=fe):(typeof f.componentDidUpdate!="function"||_===t.memoizedProps&&ie===t.memoizedState||(n.flags|=4),typeof f.getSnapshotBeforeUpdate!="function"||_===t.memoizedProps&&ie===t.memoizedState||(n.flags|=1024),o=!1)}return f=o,Bl(t,n),o=(n.flags&128)!==0,f||o?(f=n.stateNode,a=o&&typeof a.getDerivedStateFromError!="function"?null:f.render(),n.flags|=1,t!==null&&o?(n.child=bs(n,t.child,null,c),n.child=bs(n,null,a,c)):Cn(t,n,a,c),n.memoizedState=f.state,t=n.child):t=sa(t,n,c),t}function eg(t,n,a,o){return vs(),n.flags|=256,Cn(t,n,a,o),n.child}var hf={dehydrated:null,treeContext:null,retryLane:0,hydrationErrors:null};function pf(t){return{baseLanes:t,cachePool:kp()}}function mf(t,n,a){return t=t!==null?t.childLanes&~a:0,n&&(t|=ai),t}function tg(t,n,a){var o=n.pendingProps,c=!1,f=(n.flags&128)!==0,_;if((_=f)||(_=t!==null&&t.memoizedState===null?!1:(cn.current&2)!==0),_&&(c=!0,n.flags&=-129),_=(n.flags&32)!==0,n.flags&=-33,t===null){if(Mt){if(c?Ba(n):Ia(),(t=Zt)?(t=c0(t,di),t=t!==null&&t.data!=="&"?t:null,t!==null&&(n.memoizedState={dehydrated:t,treeContext:Ua!==null?{id:Pi,overflow:zi}:null,retryLane:536870912,hydrationErrors:null},a=Op(t),a.return=n,n.child=a,An=n,Zt=null)):t=null,t===null)throw Na(n);return Jf(t)?n.lanes=32:n.lanes=536870912,null}var R=o.children;return o=o.fallback,c?(Ia(),c=n.mode,R=Il({mode:"hidden",children:R},c),o=_s(o,c,a,null),R.return=n,o.return=n,R.sibling=o,n.child=R,o=n.child,o.memoizedState=pf(a),o.childLanes=mf(t,_,a),n.memoizedState=hf,xo(null,o)):(Ba(n),gf(n,R))}var P=t.memoizedState;if(P!==null&&(R=P.dehydrated,R!==null)){if(f)n.flags&256?(Ba(n),n.flags&=-257,n=_f(t,n,a)):n.memoizedState!==null?(Ia(),n.child=t.child,n.flags|=128,n=null):(Ia(),R=o.fallback,c=n.mode,o=Il({mode:"visible",children:o.children},c),R=_s(R,c,a,null),R.flags|=2,o.return=n,R.return=n,o.sibling=R,n.child=o,bs(n,t.child,null,a),o=n.child,o.memoizedState=pf(a),o.childLanes=mf(t,_,a),n.memoizedState=hf,n=xo(null,o));else if(Ba(n),Jf(R)){if(_=R.nextSibling&&R.nextSibling.dataset,_)var J=_.dgst;_=J,o=Error(r(419)),o.stack="",o.digest=_,ro({value:o,source:null,stack:null}),n=_f(t,n,a)}else if(gn||er(t,n,a,!1),_=(a&t.childLanes)!==0,gn||_){if(_=Wt,_!==null&&(o=Qn(_,a),o!==0&&o!==P.retryLane))throw P.retryLane=o,gs(t,o),qn(_,t,o),ff;Qf(R)||Zl(),n=_f(t,n,a)}else Qf(R)?(n.flags|=192,n.child=t.child,n=null):(t=P.treeContext,Zt=pi(R.nextSibling),An=n,Mt=!0,La=null,di=!1,t!==null&&Fp(n,t),n=gf(n,o.children),n.flags|=4096);return n}return c?(Ia(),R=o.fallback,c=n.mode,P=t.child,J=P.sibling,o=ji(P,{mode:"hidden",children:o.children}),o.subtreeFlags=P.subtreeFlags&65011712,J!==null?R=ji(J,R):(R=_s(R,c,a,null),R.flags|=2),R.return=n,o.return=n,o.sibling=R,n.child=o,xo(null,o),o=n.child,R=t.child.memoizedState,R===null?R=pf(a):(c=R.cachePool,c!==null?(P=pn._currentValue,c=c.parent!==P?{parent:P,pool:P}:c):c=kp(),R={baseLanes:R.baseLanes|a,cachePool:c}),o.memoizedState=R,o.childLanes=mf(t,_,a),n.memoizedState=hf,xo(t.child,o)):(Ba(n),a=t.child,t=a.sibling,a=ji(a,{mode:"visible",children:o.children}),a.return=n,a.sibling=null,t!==null&&(_=n.deletions,_===null?(n.deletions=[t],n.flags|=16):_.push(t)),n.child=a,n.memoizedState=null,a)}function gf(t,n){return n=Il({mode:"visible",children:n},t.mode),n.return=t,t.child=n}function Il(t,n){return t=ei(22,t,null,n),t.lanes=0,t}function _f(t,n,a){return bs(n,t.child,null,a),t=gf(n,n.pendingProps.children),t.flags|=2,n.memoizedState=null,t}function ng(t,n,a){t.lanes|=n;var o=t.alternate;o!==null&&(o.lanes|=n),Uu(t.return,n,a)}function vf(t,n,a,o,c,f){var _=t.memoizedState;_===null?t.memoizedState={isBackwards:n,rendering:null,renderingStartTime:0,last:o,tail:a,tailMode:c,treeForkCount:f}:(_.isBackwards=n,_.rendering=null,_.renderingStartTime=0,_.last=o,_.tail=a,_.tailMode=c,_.treeForkCount=f)}function ig(t,n,a){var o=n.pendingProps,c=o.revealOrder,f=o.tail;o=o.children;var _=cn.current,R=(_&2)!==0;if(R?(_=_&1|2,n.flags|=128):_&=1,Te(cn,_),Cn(t,n,o,a),o=Mt?so:0,!R&&t!==null&&(t.flags&128)!==0)e:for(t=n.child;t!==null;){if(t.tag===13)t.memoizedState!==null&&ng(t,a,n);else if(t.tag===19)ng(t,a,n);else if(t.child!==null){t.child.return=t,t=t.child;continue}if(t===n)break e;for(;t.sibling===null;){if(t.return===null||t.return===n)break e;t=t.return}t.sibling.return=t.return,t=t.sibling}switch(c){case"forwards":for(a=n.child,c=null;a!==null;)t=a.alternate,t!==null&&Rl(t)===null&&(c=a),a=a.sibling;a=c,a===null?(c=n.child,n.child=null):(c=a.sibling,a.sibling=null),vf(n,!1,c,a,f,o);break;case"backwards":case"unstable_legacy-backwards":for(a=null,c=n.child,n.child=null;c!==null;){if(t=c.alternate,t!==null&&Rl(t)===null){n.child=c;break}t=c.sibling,c.sibling=a,a=c,c=t}vf(n,!0,a,null,f,o);break;case"together":vf(n,!1,null,null,void 0,o);break;default:n.memoizedState=null}return n.child}function sa(t,n,a){if(t!==null&&(n.dependencies=t.dependencies),Va|=n.lanes,(a&n.childLanes)===0)if(t!==null){if(er(t,n,a,!1),(a&n.childLanes)===0)return null}else return null;if(t!==null&&n.child!==t.child)throw Error(r(153));if(n.child!==null){for(t=n.child,a=ji(t,t.pendingProps),n.child=a,a.return=n;t.sibling!==null;)t=t.sibling,a=a.sibling=ji(t,t.pendingProps),a.return=n;a.sibling=null}return n.child}function xf(t,n){return(t.lanes&n)!==0?!0:(t=t.dependencies,!!(t!==null&&xl(t)))}function Vx(t,n,a){switch(n.tag){case 3:Me(n,n.stateNode.containerInfo),Oa(n,pn,t.memoizedState.cache),vs();break;case 27:case 5:at(n);break;case 4:Me(n,n.stateNode.containerInfo);break;case 10:Oa(n,n.type,n.memoizedProps.value);break;case 31:if(n.memoizedState!==null)return n.flags|=128,ku(n),null;break;case 13:var o=n.memoizedState;if(o!==null)return o.dehydrated!==null?(Ba(n),n.flags|=128,null):(a&n.child.childLanes)!==0?tg(t,n,a):(Ba(n),t=sa(t,n,a),t!==null?t.sibling:null);Ba(n);break;case 19:var c=(t.flags&128)!==0;if(o=(a&n.childLanes)!==0,o||(er(t,n,a,!1),o=(a&n.childLanes)!==0),c){if(o)return ig(t,n,a);n.flags|=128}if(c=n.memoizedState,c!==null&&(c.rendering=null,c.tail=null,c.lastEffect=null),Te(cn,cn.current),o)break;return null;case 22:return n.lanes=0,Km(t,n,a,n.pendingProps);case 24:Oa(n,pn,t.memoizedState.cache)}return sa(t,n,a)}function ag(t,n,a){if(t!==null)if(t.memoizedProps!==n.pendingProps)gn=!0;else{if(!xf(t,a)&&(n.flags&128)===0)return gn=!1,Vx(t,n,a);gn=(t.flags&131072)!==0}else gn=!1,Mt&&(n.flags&1048576)!==0&&zp(n,so,n.index);switch(n.lanes=0,n.tag){case 16:e:{var o=n.pendingProps;if(t=ys(n.elementType),n.type=t,typeof t=="function")Eu(t)?(o=As(t,o),n.tag=1,n=jm(null,n,t,o,a)):(n.tag=0,n=df(null,n,t,o,a));else{if(t!=null){var c=t.$$typeof;if(c===C){n.tag=11,n=qm(null,n,t,o,a);break e}else if(c===B){n.tag=14,n=Ym(null,n,t,o,a);break e}}throw n=he(t)||t,Error(r(306,n,""))}}return n;case 0:return df(t,n,n.type,n.pendingProps,a);case 1:return o=n.type,c=As(o,n.pendingProps),jm(t,n,o,c,a);case 3:e:{if(Me(n,n.stateNode.containerInfo),t===null)throw Error(r(387));o=n.pendingProps;var f=n.memoizedState;c=f.element,Bu(t,n),po(n,o,null,a);var _=n.memoizedState;if(o=_.cache,Oa(n,pn,o),o!==f.cache&&Lu(n,[pn],a,!0),ho(),o=_.element,f.isDehydrated)if(f={element:o,isDehydrated:!1,cache:_.cache},n.updateQueue.baseState=f,n.memoizedState=f,n.flags&256){n=eg(t,n,o,a);break e}else if(o!==c){c=ci(Error(r(424)),n),ro(c),n=eg(t,n,o,a);break e}else{switch(t=n.stateNode.containerInfo,t.nodeType){case 9:t=t.body;break;default:t=t.nodeName==="HTML"?t.ownerDocument.body:t}for(Zt=pi(t.firstChild),An=n,Mt=!0,La=null,di=!0,a=Kp(n,null,o,a),n.child=a;a;)a.flags=a.flags&-3|4096,a=a.sibling}else{if(vs(),o===c){n=sa(t,n,a);break e}Cn(t,n,o,a)}n=n.child}return n;case 26:return Bl(t,n),t===null?(a=m0(n.type,null,n.pendingProps,null))?n.memoizedState=a:Mt||(a=n.type,t=n.pendingProps,o=tc(ue.current).createElement(a),o[dn]=n,o[Tn]=t,wn(o,a,t),hn(o),n.stateNode=o):n.memoizedState=m0(n.type,t.memoizedProps,n.pendingProps,t.memoizedState),null;case 27:return at(n),t===null&&Mt&&(o=n.stateNode=d0(n.type,n.pendingProps,ue.current),An=n,di=!0,c=Zt,Ya(n.type)?($f=c,Zt=pi(o.firstChild)):Zt=c),Cn(t,n,n.pendingProps.children,a),Bl(t,n),t===null&&(n.flags|=4194304),n.child;case 5:return t===null&&Mt&&((c=o=Zt)&&(o=vS(o,n.type,n.pendingProps,di),o!==null?(n.stateNode=o,An=n,Zt=pi(o.firstChild),di=!1,c=!0):c=!1),c||Na(n)),at(n),c=n.type,f=n.pendingProps,_=t!==null?t.memoizedProps:null,o=f.children,Yf(c,f)?o=null:_!==null&&Yf(c,_)&&(n.flags|=32),n.memoizedState!==null&&(c=Wu(t,n,Nx,null,null,a),Oo._currentValue=c),Bl(t,n),Cn(t,n,o,a),n.child;case 6:return t===null&&Mt&&((t=a=Zt)&&(a=xS(a,n.pendingProps,di),a!==null?(n.stateNode=a,An=n,Zt=null,t=!0):t=!1),t||Na(n)),null;case 13:return tg(t,n,a);case 4:return Me(n,n.stateNode.containerInfo),o=n.pendingProps,t===null?n.child=bs(n,null,o,a):Cn(t,n,o,a),n.child;case 11:return qm(t,n,n.type,n.pendingProps,a);case 7:return Cn(t,n,n.pendingProps,a),n.child;case 8:return Cn(t,n,n.pendingProps.children,a),n.child;case 12:return Cn(t,n,n.pendingProps.children,a),n.child;case 10:return o=n.pendingProps,Oa(n,n.type,o.value),Cn(t,n,o.children,a),n.child;case 9:return c=n.type._context,o=n.pendingProps.children,Ss(n),c=Rn(c),o=o(c),n.flags|=1,Cn(t,n,o,a),n.child;case 14:return Ym(t,n,n.type,n.pendingProps,a);case 15:return Zm(t,n,n.type,n.pendingProps,a);case 19:return ig(t,n,a);case 31:return Gx(t,n,a);case 22:return Km(t,n,a,n.pendingProps);case 24:return Ss(n),o=Rn(pn),t===null?(c=Pu(),c===null&&(c=Wt,f=Nu(),c.pooledCache=f,f.refCount++,f!==null&&(c.pooledCacheLanes|=a),c=f),n.memoizedState={parent:o,cache:c},Fu(n),Oa(n,pn,c)):((t.lanes&a)!==0&&(Bu(t,n),po(n,null,null,a),ho()),c=t.memoizedState,f=n.memoizedState,c.parent!==o?(c={parent:o,cache:o},n.memoizedState=c,n.lanes===0&&(n.memoizedState=n.updateQueue.baseState=c),Oa(n,pn,o)):(o=f.cache,Oa(n,pn,o),o!==c.cache&&Lu(n,[pn],a,!0))),Cn(t,n,n.pendingProps.children,a),n.child;case 29:throw n.pendingProps}throw Error(r(156,n.tag))}function ra(t){t.flags|=4}function Sf(t,n,a,o,c){if((n=(t.mode&32)!==0)&&(n=!1),n){if(t.flags|=16777216,(c&335544128)===c)if(t.stateNode.complete)t.flags|=8192;else if(Ug())t.flags|=8192;else throw Es=El,zu}else t.flags&=-16777217}function sg(t,n){if(n.type!=="stylesheet"||(n.state.loading&4)!==0)t.flags&=-16777217;else if(t.flags|=16777216,!S0(n))if(Ug())t.flags|=8192;else throw Es=El,zu}function Hl(t,n){n!==null&&(t.flags|=4),t.flags&16384&&(n=t.tag!==22?ve():536870912,t.lanes|=n,dr|=n)}function So(t,n){if(!Mt)switch(t.tailMode){case"hidden":n=t.tail;for(var a=null;n!==null;)n.alternate!==null&&(a=n),n=n.sibling;a===null?t.tail=null:a.sibling=null;break;case"collapsed":a=t.tail;for(var o=null;a!==null;)a.alternate!==null&&(o=a),a=a.sibling;o===null?n||t.tail===null?t.tail=null:t.tail.sibling=null:o.sibling=null}}function Kt(t){var n=t.alternate!==null&&t.alternate.child===t.child,a=0,o=0;if(n)for(var c=t.child;c!==null;)a|=c.lanes|c.childLanes,o|=c.subtreeFlags&65011712,o|=c.flags&65011712,c.return=t,c=c.sibling;else for(c=t.child;c!==null;)a|=c.lanes|c.childLanes,o|=c.subtreeFlags,o|=c.flags,c.return=t,c=c.sibling;return t.subtreeFlags|=o,t.childLanes=a,n}function kx(t,n,a){var o=n.pendingProps;switch(Ru(n),n.tag){case 16:case 15:case 0:case 11:case 7:case 8:case 12:case 9:case 14:return Kt(n),null;case 1:return Kt(n),null;case 3:return a=n.stateNode,o=null,t!==null&&(o=t.memoizedState.cache),n.memoizedState.cache!==o&&(n.flags|=2048),na(pn),$e(),a.pendingContext&&(a.context=a.pendingContext,a.pendingContext=null),(t===null||t.child===null)&&(js(n)?ra(n):t===null||t.memoizedState.isDehydrated&&(n.flags&256)===0||(n.flags|=1024,wu())),Kt(n),null;case 26:var c=n.type,f=n.memoizedState;return t===null?(ra(n),f!==null?(Kt(n),sg(n,f)):(Kt(n),Sf(n,c,null,o,a))):f?f!==t.memoizedState?(ra(n),Kt(n),sg(n,f)):(Kt(n),n.flags&=-16777217):(t=t.memoizedProps,t!==o&&ra(n),Kt(n),Sf(n,c,t,o,a)),null;case 27:if(Ze(n),a=ue.current,c=n.type,t!==null&&n.stateNode!=null)t.memoizedProps!==o&&ra(n);else{if(!o){if(n.stateNode===null)throw Error(r(166));return Kt(n),null}t=tt.current,js(n)?Bp(n):(t=d0(c,o,a),n.stateNode=t,ra(n))}return Kt(n),null;case 5:if(Ze(n),c=n.type,t!==null&&n.stateNode!=null)t.memoizedProps!==o&&ra(n);else{if(!o){if(n.stateNode===null)throw Error(r(166));return Kt(n),null}if(f=tt.current,js(n))Bp(n);else{var _=tc(ue.current);switch(f){case 1:f=_.createElementNS("http://www.w3.org/2000/svg",c);break;case 2:f=_.createElementNS("http://www.w3.org/1998/Math/MathML",c);break;default:switch(c){case"svg":f=_.createElementNS("http://www.w3.org/2000/svg",c);break;case"math":f=_.createElementNS("http://www.w3.org/1998/Math/MathML",c);break;case"script":f=_.createElement("div"),f.innerHTML="<script><\/script>",f=f.removeChild(f.firstChild);break;case"select":f=typeof o.is=="string"?_.createElement("select",{is:o.is}):_.createElement("select"),o.multiple?f.multiple=!0:o.size&&(f.size=o.size);break;default:f=typeof o.is=="string"?_.createElement(c,{is:o.is}):_.createElement(c)}}f[dn]=n,f[Tn]=o;e:for(_=n.child;_!==null;){if(_.tag===5||_.tag===6)f.appendChild(_.stateNode);else if(_.tag!==4&&_.tag!==27&&_.child!==null){_.child.return=_,_=_.child;continue}if(_===n)break e;for(;_.sibling===null;){if(_.return===null||_.return===n)break e;_=_.return}_.sibling.return=_.return,_=_.sibling}n.stateNode=f;e:switch(wn(f,c,o),c){case"button":case"input":case"select":case"textarea":o=!!o.autoFocus;break e;case"img":o=!0;break e;default:o=!1}o&&ra(n)}}return Kt(n),Sf(n,n.type,t===null?null:t.memoizedProps,n.pendingProps,a),null;case 6:if(t&&n.stateNode!=null)t.memoizedProps!==o&&ra(n);else{if(typeof o!="string"&&n.stateNode===null)throw Error(r(166));if(t=ue.current,js(n)){if(t=n.stateNode,a=n.memoizedProps,o=null,c=An,c!==null)switch(c.tag){case 27:case 5:o=c.memoizedProps}t[dn]=n,t=!!(t.nodeValue===a||o!==null&&o.suppressHydrationWarning===!0||t0(t.nodeValue,a)),t||Na(n,!0)}else t=tc(t).createTextNode(o),t[dn]=n,n.stateNode=t}return Kt(n),null;case 31:if(a=n.memoizedState,t===null||t.memoizedState!==null){if(o=js(n),a!==null){if(t===null){if(!o)throw Error(r(318));if(t=n.memoizedState,t=t!==null?t.dehydrated:null,!t)throw Error(r(557));t[dn]=n}else vs(),(n.flags&128)===0&&(n.memoizedState=null),n.flags|=4;Kt(n),t=!1}else a=wu(),t!==null&&t.memoizedState!==null&&(t.memoizedState.hydrationErrors=a),t=!0;if(!t)return n.flags&256?(ni(n),n):(ni(n),null);if((n.flags&128)!==0)throw Error(r(558))}return Kt(n),null;case 13:if(o=n.memoizedState,t===null||t.memoizedState!==null&&t.memoizedState.dehydrated!==null){if(c=js(n),o!==null&&o.dehydrated!==null){if(t===null){if(!c)throw Error(r(318));if(c=n.memoizedState,c=c!==null?c.dehydrated:null,!c)throw Error(r(317));c[dn]=n}else vs(),(n.flags&128)===0&&(n.memoizedState=null),n.flags|=4;Kt(n),c=!1}else c=wu(),t!==null&&t.memoizedState!==null&&(t.memoizedState.hydrationErrors=c),c=!0;if(!c)return n.flags&256?(ni(n),n):(ni(n),null)}return ni(n),(n.flags&128)!==0?(n.lanes=a,n):(a=o!==null,t=t!==null&&t.memoizedState!==null,a&&(o=n.child,c=null,o.alternate!==null&&o.alternate.memoizedState!==null&&o.alternate.memoizedState.cachePool!==null&&(c=o.alternate.memoizedState.cachePool.pool),f=null,o.memoizedState!==null&&o.memoizedState.cachePool!==null&&(f=o.memoizedState.cachePool.pool),f!==c&&(o.flags|=2048)),a!==t&&a&&(n.child.flags|=8192),Hl(n,n.updateQueue),Kt(n),null);case 4:return $e(),t===null&&Vf(n.stateNode.containerInfo),Kt(n),null;case 10:return na(n.type),Kt(n),null;case 19:if(Se(cn),o=n.memoizedState,o===null)return Kt(n),null;if(c=(n.flags&128)!==0,f=o.rendering,f===null)if(c)So(o,!1);else{if(rn!==0||t!==null&&(t.flags&128)!==0)for(t=n.child;t!==null;){if(f=Rl(t),f!==null){for(n.flags|=128,So(o,!1),t=f.updateQueue,n.updateQueue=t,Hl(n,t),n.subtreeFlags=0,t=a,a=n.child;a!==null;)Np(a,t),a=a.sibling;return Te(cn,cn.current&1|2),Mt&&ea(n,o.treeForkCount),n.child}t=t.sibling}o.tail!==null&&Ht()>Wl&&(n.flags|=128,c=!0,So(o,!1),n.lanes=4194304)}else{if(!c)if(t=Rl(f),t!==null){if(n.flags|=128,c=!0,t=t.updateQueue,n.updateQueue=t,Hl(n,t),So(o,!0),o.tail===null&&o.tailMode==="hidden"&&!f.alternate&&!Mt)return Kt(n),null}else 2*Ht()-o.renderingStartTime>Wl&&a!==536870912&&(n.flags|=128,c=!0,So(o,!1),n.lanes=4194304);o.isBackwards?(f.sibling=n.child,n.child=f):(t=o.last,t!==null?t.sibling=f:n.child=f,o.last=f)}return o.tail!==null?(t=o.tail,o.rendering=t,o.tail=t.sibling,o.renderingStartTime=Ht(),t.sibling=null,a=cn.current,Te(cn,c?a&1|2:a&1),Mt&&ea(n,o.treeForkCount),t):(Kt(n),null);case 22:case 23:return ni(n),Vu(),o=n.memoizedState!==null,t!==null?t.memoizedState!==null!==o&&(n.flags|=8192):o&&(n.flags|=8192),o?(a&536870912)!==0&&(n.flags&128)===0&&(Kt(n),n.subtreeFlags&6&&(n.flags|=8192)):Kt(n),a=n.updateQueue,a!==null&&Hl(n,a.retryQueue),a=null,t!==null&&t.memoizedState!==null&&t.memoizedState.cachePool!==null&&(a=t.memoizedState.cachePool.pool),o=null,n.memoizedState!==null&&n.memoizedState.cachePool!==null&&(o=n.memoizedState.cachePool.pool),o!==a&&(n.flags|=2048),t!==null&&Se(Ms),null;case 24:return a=null,t!==null&&(a=t.memoizedState.cache),n.memoizedState.cache!==a&&(n.flags|=2048),na(pn),Kt(n),null;case 25:return null;case 30:return null}throw Error(r(156,n.tag))}function Xx(t,n){switch(Ru(n),n.tag){case 1:return t=n.flags,t&65536?(n.flags=t&-65537|128,n):null;case 3:return na(pn),$e(),t=n.flags,(t&65536)!==0&&(t&128)===0?(n.flags=t&-65537|128,n):null;case 26:case 27:case 5:return Ze(n),null;case 31:if(n.memoizedState!==null){if(ni(n),n.alternate===null)throw Error(r(340));vs()}return t=n.flags,t&65536?(n.flags=t&-65537|128,n):null;case 13:if(ni(n),t=n.memoizedState,t!==null&&t.dehydrated!==null){if(n.alternate===null)throw Error(r(340));vs()}return t=n.flags,t&65536?(n.flags=t&-65537|128,n):null;case 19:return Se(cn),null;case 4:return $e(),null;case 10:return na(n.type),null;case 22:case 23:return ni(n),Vu(),t!==null&&Se(Ms),t=n.flags,t&65536?(n.flags=t&-65537|128,n):null;case 24:return na(pn),null;case 25:return null;default:return null}}function rg(t,n){switch(Ru(n),n.tag){case 3:na(pn),$e();break;case 26:case 27:case 5:Ze(n);break;case 4:$e();break;case 31:n.memoizedState!==null&&ni(n);break;case 13:ni(n);break;case 19:Se(cn);break;case 10:na(n.type);break;case 22:case 23:ni(n),Vu(),t!==null&&Se(Ms);break;case 24:na(pn)}}function Mo(t,n){try{var a=n.updateQueue,o=a!==null?a.lastEffect:null;if(o!==null){var c=o.next;a=c;do{if((a.tag&t)===t){o=void 0;var f=a.create,_=a.inst;o=f(),_.destroy=o}a=a.next}while(a!==c)}}catch(R){Bt(n,n.return,R)}}function Ha(t,n,a){try{var o=n.updateQueue,c=o!==null?o.lastEffect:null;if(c!==null){var f=c.next;o=f;do{if((o.tag&t)===t){var _=o.inst,R=_.destroy;if(R!==void 0){_.destroy=void 0,c=n;var P=a,J=R;try{J()}catch(fe){Bt(c,P,fe)}}}o=o.next}while(o!==f)}}catch(fe){Bt(n,n.return,fe)}}function og(t){var n=t.updateQueue;if(n!==null){var a=t.stateNode;try{Jp(n,a)}catch(o){Bt(t,t.return,o)}}}function lg(t,n,a){a.props=As(t.type,t.memoizedProps),a.state=t.memoizedState;try{a.componentWillUnmount()}catch(o){Bt(t,n,o)}}function yo(t,n){try{var a=t.ref;if(a!==null){switch(t.tag){case 26:case 27:case 5:var o=t.stateNode;break;case 30:o=t.stateNode;break;default:o=t.stateNode}typeof a=="function"?t.refCleanup=a(o):a.current=o}}catch(c){Bt(t,n,c)}}function Fi(t,n){var a=t.ref,o=t.refCleanup;if(a!==null)if(typeof o=="function")try{o()}catch(c){Bt(t,n,c)}finally{t.refCleanup=null,t=t.alternate,t!=null&&(t.refCleanup=null)}else if(typeof a=="function")try{a(null)}catch(c){Bt(t,n,c)}else a.current=null}function cg(t){var n=t.type,a=t.memoizedProps,o=t.stateNode;try{e:switch(n){case"button":case"input":case"select":case"textarea":a.autoFocus&&o.focus();break e;case"img":a.src?o.src=a.src:a.srcSet&&(o.srcset=a.srcSet)}}catch(c){Bt(t,t.return,c)}}function Mf(t,n,a){try{var o=t.stateNode;dS(o,t.type,a,n),o[Tn]=n}catch(c){Bt(t,t.return,c)}}function ug(t){return t.tag===5||t.tag===3||t.tag===26||t.tag===27&&Ya(t.type)||t.tag===4}function yf(t){e:for(;;){for(;t.sibling===null;){if(t.return===null||ug(t.return))return null;t=t.return}for(t.sibling.return=t.return,t=t.sibling;t.tag!==5&&t.tag!==6&&t.tag!==18;){if(t.tag===27&&Ya(t.type)||t.flags&2||t.child===null||t.tag===4)continue e;t.child.return=t,t=t.child}if(!(t.flags&2))return t.stateNode}}function Ef(t,n,a){var o=t.tag;if(o===5||o===6)t=t.stateNode,n?(a.nodeType===9?a.body:a.nodeName==="HTML"?a.ownerDocument.body:a).insertBefore(t,n):(n=a.nodeType===9?a.body:a.nodeName==="HTML"?a.ownerDocument.body:a,n.appendChild(t),a=a._reactRootContainer,a!=null||n.onclick!==null||(n.onclick=Ji));else if(o!==4&&(o===27&&Ya(t.type)&&(a=t.stateNode,n=null),t=t.child,t!==null))for(Ef(t,n,a),t=t.sibling;t!==null;)Ef(t,n,a),t=t.sibling}function Gl(t,n,a){var o=t.tag;if(o===5||o===6)t=t.stateNode,n?a.insertBefore(t,n):a.appendChild(t);else if(o!==4&&(o===27&&Ya(t.type)&&(a=t.stateNode),t=t.child,t!==null))for(Gl(t,n,a),t=t.sibling;t!==null;)Gl(t,n,a),t=t.sibling}function fg(t){var n=t.stateNode,a=t.memoizedProps;try{for(var o=t.type,c=n.attributes;c.length;)n.removeAttributeNode(c[0]);wn(n,o,a),n[dn]=t,n[Tn]=a}catch(f){Bt(t,t.return,f)}}var oa=!1,_n=!1,bf=!1,dg=typeof WeakSet=="function"?WeakSet:Set,En=null;function Wx(t,n){if(t=t.containerInfo,Wf=lc,t=bp(t),gu(t)){if("selectionStart"in t)var a={start:t.selectionStart,end:t.selectionEnd};else e:{a=(a=t.ownerDocument)&&a.defaultView||window;var o=a.getSelection&&a.getSelection();if(o&&o.rangeCount!==0){a=o.anchorNode;var c=o.anchorOffset,f=o.focusNode;o=o.focusOffset;try{a.nodeType,f.nodeType}catch{a=null;break e}var _=0,R=-1,P=-1,J=0,fe=0,_e=t,ie=null;t:for(;;){for(var re;_e!==a||c!==0&&_e.nodeType!==3||(R=_+c),_e!==f||o!==0&&_e.nodeType!==3||(P=_+o),_e.nodeType===3&&(_+=_e.nodeValue.length),(re=_e.firstChild)!==null;)ie=_e,_e=re;for(;;){if(_e===t)break t;if(ie===a&&++J===c&&(R=_),ie===f&&++fe===o&&(P=_),(re=_e.nextSibling)!==null)break;_e=ie,ie=_e.parentNode}_e=re}a=R===-1||P===-1?null:{start:R,end:P}}else a=null}a=a||{start:0,end:0}}else a=null;for(qf={focusedElem:t,selectionRange:a},lc=!1,En=n;En!==null;)if(n=En,t=n.child,(n.subtreeFlags&1028)!==0&&t!==null)t.return=n,En=t;else for(;En!==null;){switch(n=En,f=n.alternate,t=n.flags,n.tag){case 0:if((t&4)!==0&&(t=n.updateQueue,t=t!==null?t.events:null,t!==null))for(a=0;a<t.length;a++)c=t[a],c.ref.impl=c.nextImpl;break;case 11:case 15:break;case 1:if((t&1024)!==0&&f!==null){t=void 0,a=n,c=f.memoizedProps,f=f.memoizedState,o=a.stateNode;try{var Ve=As(a.type,c);t=o.getSnapshotBeforeUpdate(Ve,f),o.__reactInternalSnapshotBeforeUpdate=t}catch(je){Bt(a,a.return,je)}}break;case 3:if((t&1024)!==0){if(t=n.stateNode.containerInfo,a=t.nodeType,a===9)Kf(t);else if(a===1)switch(t.nodeName){case"HEAD":case"HTML":case"BODY":Kf(t);break;default:t.textContent=""}}break;case 5:case 26:case 27:case 6:case 4:case 17:break;default:if((t&1024)!==0)throw Error(r(163))}if(t=n.sibling,t!==null){t.return=n.return,En=t;break}En=n.return}}function hg(t,n,a){var o=a.flags;switch(a.tag){case 0:case 11:case 15:ca(t,a),o&4&&Mo(5,a);break;case 1:if(ca(t,a),o&4)if(t=a.stateNode,n===null)try{t.componentDidMount()}catch(_){Bt(a,a.return,_)}else{var c=As(a.type,n.memoizedProps);n=n.memoizedState;try{t.componentDidUpdate(c,n,t.__reactInternalSnapshotBeforeUpdate)}catch(_){Bt(a,a.return,_)}}o&64&&og(a),o&512&&yo(a,a.return);break;case 3:if(ca(t,a),o&64&&(t=a.updateQueue,t!==null)){if(n=null,a.child!==null)switch(a.child.tag){case 27:case 5:n=a.child.stateNode;break;case 1:n=a.child.stateNode}try{Jp(t,n)}catch(_){Bt(a,a.return,_)}}break;case 27:n===null&&o&4&&fg(a);case 26:case 5:ca(t,a),n===null&&o&4&&cg(a),o&512&&yo(a,a.return);break;case 12:ca(t,a);break;case 31:ca(t,a),o&4&&gg(t,a);break;case 13:ca(t,a),o&4&&_g(t,a),o&64&&(t=a.memoizedState,t!==null&&(t=t.dehydrated,t!==null&&(a=eS.bind(null,a),SS(t,a))));break;case 22:if(o=a.memoizedState!==null||oa,!o){n=n!==null&&n.memoizedState!==null||_n,c=oa;var f=_n;oa=o,(_n=n)&&!f?ua(t,a,(a.subtreeFlags&8772)!==0):ca(t,a),oa=c,_n=f}break;case 30:break;default:ca(t,a)}}function pg(t){var n=t.alternate;n!==null&&(t.alternate=null,pg(n)),t.child=null,t.deletions=null,t.sibling=null,t.tag===5&&(n=t.stateNode,n!==null&&Ra(n)),t.stateNode=null,t.return=null,t.dependencies=null,t.memoizedProps=null,t.memoizedState=null,t.pendingProps=null,t.stateNode=null,t.updateQueue=null}var jt=null,Vn=!1;function la(t,n,a){for(a=a.child;a!==null;)mg(t,n,a),a=a.sibling}function mg(t,n,a){if(ce&&typeof ce.onCommitFiberUnmount=="function")try{ce.onCommitFiberUnmount(oe,a)}catch{}switch(a.tag){case 26:_n||Fi(a,n),la(t,n,a),a.memoizedState?a.memoizedState.count--:a.stateNode&&(a=a.stateNode,a.parentNode.removeChild(a));break;case 27:_n||Fi(a,n);var o=jt,c=Vn;Ya(a.type)&&(jt=a.stateNode,Vn=!1),la(t,n,a),Uo(a.stateNode),jt=o,Vn=c;break;case 5:_n||Fi(a,n);case 6:if(o=jt,c=Vn,jt=null,la(t,n,a),jt=o,Vn=c,jt!==null)if(Vn)try{(jt.nodeType===9?jt.body:jt.nodeName==="HTML"?jt.ownerDocument.body:jt).removeChild(a.stateNode)}catch(f){Bt(a,n,f)}else try{jt.removeChild(a.stateNode)}catch(f){Bt(a,n,f)}break;case 18:jt!==null&&(Vn?(t=jt,o0(t.nodeType===9?t.body:t.nodeName==="HTML"?t.ownerDocument.body:t,a.stateNode),Sr(t)):o0(jt,a.stateNode));break;case 4:o=jt,c=Vn,jt=a.stateNode.containerInfo,Vn=!0,la(t,n,a),jt=o,Vn=c;break;case 0:case 11:case 14:case 15:Ha(2,a,n),_n||Ha(4,a,n),la(t,n,a);break;case 1:_n||(Fi(a,n),o=a.stateNode,typeof o.componentWillUnmount=="function"&&lg(a,n,o)),la(t,n,a);break;case 21:la(t,n,a);break;case 22:_n=(o=_n)||a.memoizedState!==null,la(t,n,a),_n=o;break;default:la(t,n,a)}}function gg(t,n){if(n.memoizedState===null&&(t=n.alternate,t!==null&&(t=t.memoizedState,t!==null))){t=t.dehydrated;try{Sr(t)}catch(a){Bt(n,n.return,a)}}}function _g(t,n){if(n.memoizedState===null&&(t=n.alternate,t!==null&&(t=t.memoizedState,t!==null&&(t=t.dehydrated,t!==null))))try{Sr(t)}catch(a){Bt(n,n.return,a)}}function qx(t){switch(t.tag){case 31:case 13:case 19:var n=t.stateNode;return n===null&&(n=t.stateNode=new dg),n;case 22:return t=t.stateNode,n=t._retryCache,n===null&&(n=t._retryCache=new dg),n;default:throw Error(r(435,t.tag))}}function Vl(t,n){var a=qx(t);n.forEach(function(o){if(!a.has(o)){a.add(o);var c=tS.bind(null,t,o);o.then(c,c)}})}function kn(t,n){var a=n.deletions;if(a!==null)for(var o=0;o<a.length;o++){var c=a[o],f=t,_=n,R=_;e:for(;R!==null;){switch(R.tag){case 27:if(Ya(R.type)){jt=R.stateNode,Vn=!1;break e}break;case 5:jt=R.stateNode,Vn=!1;break e;case 3:case 4:jt=R.stateNode.containerInfo,Vn=!0;break e}R=R.return}if(jt===null)throw Error(r(160));mg(f,_,c),jt=null,Vn=!1,f=c.alternate,f!==null&&(f.return=null),c.return=null}if(n.subtreeFlags&13886)for(n=n.child;n!==null;)vg(n,t),n=n.sibling}var bi=null;function vg(t,n){var a=t.alternate,o=t.flags;switch(t.tag){case 0:case 11:case 14:case 15:kn(n,t),Xn(t),o&4&&(Ha(3,t,t.return),Mo(3,t),Ha(5,t,t.return));break;case 1:kn(n,t),Xn(t),o&512&&(_n||a===null||Fi(a,a.return)),o&64&&oa&&(t=t.updateQueue,t!==null&&(o=t.callbacks,o!==null&&(a=t.shared.hiddenCallbacks,t.shared.hiddenCallbacks=a===null?o:a.concat(o))));break;case 26:var c=bi;if(kn(n,t),Xn(t),o&512&&(_n||a===null||Fi(a,a.return)),o&4){var f=a!==null?a.memoizedState:null;if(o=t.memoizedState,a===null)if(o===null)if(t.stateNode===null){e:{o=t.type,a=t.memoizedProps,c=c.ownerDocument||c;t:switch(o){case"title":f=c.getElementsByTagName("title")[0],(!f||f[Aa]||f[dn]||f.namespaceURI==="http://www.w3.org/2000/svg"||f.hasAttribute("itemprop"))&&(f=c.createElement(o),c.head.insertBefore(f,c.querySelector("head > title"))),wn(f,o,a),f[dn]=t,hn(f),o=f;break e;case"link":var _=v0("link","href",c).get(o+(a.href||""));if(_){for(var R=0;R<_.length;R++)if(f=_[R],f.getAttribute("href")===(a.href==null||a.href===""?null:a.href)&&f.getAttribute("rel")===(a.rel==null?null:a.rel)&&f.getAttribute("title")===(a.title==null?null:a.title)&&f.getAttribute("crossorigin")===(a.crossOrigin==null?null:a.crossOrigin)){_.splice(R,1);break t}}f=c.createElement(o),wn(f,o,a),c.head.appendChild(f);break;case"meta":if(_=v0("meta","content",c).get(o+(a.content||""))){for(R=0;R<_.length;R++)if(f=_[R],f.getAttribute("content")===(a.content==null?null:""+a.content)&&f.getAttribute("name")===(a.name==null?null:a.name)&&f.getAttribute("property")===(a.property==null?null:a.property)&&f.getAttribute("http-equiv")===(a.httpEquiv==null?null:a.httpEquiv)&&f.getAttribute("charset")===(a.charSet==null?null:a.charSet)){_.splice(R,1);break t}}f=c.createElement(o),wn(f,o,a),c.head.appendChild(f);break;default:throw Error(r(468,o))}f[dn]=t,hn(f),o=f}t.stateNode=o}else x0(c,t.type,t.stateNode);else t.stateNode=_0(c,o,t.memoizedProps);else f!==o?(f===null?a.stateNode!==null&&(a=a.stateNode,a.parentNode.removeChild(a)):f.count--,o===null?x0(c,t.type,t.stateNode):_0(c,o,t.memoizedProps)):o===null&&t.stateNode!==null&&Mf(t,t.memoizedProps,a.memoizedProps)}break;case 27:kn(n,t),Xn(t),o&512&&(_n||a===null||Fi(a,a.return)),a!==null&&o&4&&Mf(t,t.memoizedProps,a.memoizedProps);break;case 5:if(kn(n,t),Xn(t),o&512&&(_n||a===null||Fi(a,a.return)),t.flags&32){c=t.stateNode;try{$n(c,"")}catch(Ve){Bt(t,t.return,Ve)}}o&4&&t.stateNode!=null&&(c=t.memoizedProps,Mf(t,c,a!==null?a.memoizedProps:c)),o&1024&&(bf=!0);break;case 6:if(kn(n,t),Xn(t),o&4){if(t.stateNode===null)throw Error(r(162));o=t.memoizedProps,a=t.stateNode;try{a.nodeValue=o}catch(Ve){Bt(t,t.return,Ve)}}break;case 3:if(ac=null,c=bi,bi=nc(n.containerInfo),kn(n,t),bi=c,Xn(t),o&4&&a!==null&&a.memoizedState.isDehydrated)try{Sr(n.containerInfo)}catch(Ve){Bt(t,t.return,Ve)}bf&&(bf=!1,xg(t));break;case 4:o=bi,bi=nc(t.stateNode.containerInfo),kn(n,t),Xn(t),bi=o;break;case 12:kn(n,t),Xn(t);break;case 31:kn(n,t),Xn(t),o&4&&(o=t.updateQueue,o!==null&&(t.updateQueue=null,Vl(t,o)));break;case 13:kn(n,t),Xn(t),t.child.flags&8192&&t.memoizedState!==null!=(a!==null&&a.memoizedState!==null)&&(Xl=Ht()),o&4&&(o=t.updateQueue,o!==null&&(t.updateQueue=null,Vl(t,o)));break;case 22:c=t.memoizedState!==null;var P=a!==null&&a.memoizedState!==null,J=oa,fe=_n;if(oa=J||c,_n=fe||P,kn(n,t),_n=fe,oa=J,Xn(t),o&8192)e:for(n=t.stateNode,n._visibility=c?n._visibility&-2:n._visibility|1,c&&(a===null||P||oa||_n||Rs(t)),a=null,n=t;;){if(n.tag===5||n.tag===26){if(a===null){P=a=n;try{if(f=P.stateNode,c)_=f.style,typeof _.setProperty=="function"?_.setProperty("display","none","important"):_.display="none";else{R=P.stateNode;var _e=P.memoizedProps.style,ie=_e!=null&&_e.hasOwnProperty("display")?_e.display:null;R.style.display=ie==null||typeof ie=="boolean"?"":(""+ie).trim()}}catch(Ve){Bt(P,P.return,Ve)}}}else if(n.tag===6){if(a===null){P=n;try{P.stateNode.nodeValue=c?"":P.memoizedProps}catch(Ve){Bt(P,P.return,Ve)}}}else if(n.tag===18){if(a===null){P=n;try{var re=P.stateNode;c?l0(re,!0):l0(P.stateNode,!1)}catch(Ve){Bt(P,P.return,Ve)}}}else if((n.tag!==22&&n.tag!==23||n.memoizedState===null||n===t)&&n.child!==null){n.child.return=n,n=n.child;continue}if(n===t)break e;for(;n.sibling===null;){if(n.return===null||n.return===t)break e;a===n&&(a=null),n=n.return}a===n&&(a=null),n.sibling.return=n.return,n=n.sibling}o&4&&(o=t.updateQueue,o!==null&&(a=o.retryQueue,a!==null&&(o.retryQueue=null,Vl(t,a))));break;case 19:kn(n,t),Xn(t),o&4&&(o=t.updateQueue,o!==null&&(t.updateQueue=null,Vl(t,o)));break;case 30:break;case 21:break;default:kn(n,t),Xn(t)}}function Xn(t){var n=t.flags;if(n&2){try{for(var a,o=t.return;o!==null;){if(ug(o)){a=o;break}o=o.return}if(a==null)throw Error(r(160));switch(a.tag){case 27:var c=a.stateNode,f=yf(t);Gl(t,f,c);break;case 5:var _=a.stateNode;a.flags&32&&($n(_,""),a.flags&=-33);var R=yf(t);Gl(t,R,_);break;case 3:case 4:var P=a.stateNode.containerInfo,J=yf(t);Ef(t,J,P);break;default:throw Error(r(161))}}catch(fe){Bt(t,t.return,fe)}t.flags&=-3}n&4096&&(t.flags&=-4097)}function xg(t){if(t.subtreeFlags&1024)for(t=t.child;t!==null;){var n=t;xg(n),n.tag===5&&n.flags&1024&&n.stateNode.reset(),t=t.sibling}}function ca(t,n){if(n.subtreeFlags&8772)for(n=n.child;n!==null;)hg(t,n.alternate,n),n=n.sibling}function Rs(t){for(t=t.child;t!==null;){var n=t;switch(n.tag){case 0:case 11:case 14:case 15:Ha(4,n,n.return),Rs(n);break;case 1:Fi(n,n.return);var a=n.stateNode;typeof a.componentWillUnmount=="function"&&lg(n,n.return,a),Rs(n);break;case 27:Uo(n.stateNode);case 26:case 5:Fi(n,n.return),Rs(n);break;case 22:n.memoizedState===null&&Rs(n);break;case 30:Rs(n);break;default:Rs(n)}t=t.sibling}}function ua(t,n,a){for(a=a&&(n.subtreeFlags&8772)!==0,n=n.child;n!==null;){var o=n.alternate,c=t,f=n,_=f.flags;switch(f.tag){case 0:case 11:case 15:ua(c,f,a),Mo(4,f);break;case 1:if(ua(c,f,a),o=f,c=o.stateNode,typeof c.componentDidMount=="function")try{c.componentDidMount()}catch(J){Bt(o,o.return,J)}if(o=f,c=o.updateQueue,c!==null){var R=o.stateNode;try{var P=c.shared.hiddenCallbacks;if(P!==null)for(c.shared.hiddenCallbacks=null,c=0;c<P.length;c++)Qp(P[c],R)}catch(J){Bt(o,o.return,J)}}a&&_&64&&og(f),yo(f,f.return);break;case 27:fg(f);case 26:case 5:ua(c,f,a),a&&o===null&&_&4&&cg(f),yo(f,f.return);break;case 12:ua(c,f,a);break;case 31:ua(c,f,a),a&&_&4&&gg(c,f);break;case 13:ua(c,f,a),a&&_&4&&_g(c,f);break;case 22:f.memoizedState===null&&ua(c,f,a),yo(f,f.return);break;case 30:break;default:ua(c,f,a)}n=n.sibling}}function Tf(t,n){var a=null;t!==null&&t.memoizedState!==null&&t.memoizedState.cachePool!==null&&(a=t.memoizedState.cachePool.pool),t=null,n.memoizedState!==null&&n.memoizedState.cachePool!==null&&(t=n.memoizedState.cachePool.pool),t!==a&&(t!=null&&t.refCount++,a!=null&&oo(a))}function Af(t,n){t=null,n.alternate!==null&&(t=n.alternate.memoizedState.cache),n=n.memoizedState.cache,n!==t&&(n.refCount++,t!=null&&oo(t))}function Ti(t,n,a,o){if(n.subtreeFlags&10256)for(n=n.child;n!==null;)Sg(t,n,a,o),n=n.sibling}function Sg(t,n,a,o){var c=n.flags;switch(n.tag){case 0:case 11:case 15:Ti(t,n,a,o),c&2048&&Mo(9,n);break;case 1:Ti(t,n,a,o);break;case 3:Ti(t,n,a,o),c&2048&&(t=null,n.alternate!==null&&(t=n.alternate.memoizedState.cache),n=n.memoizedState.cache,n!==t&&(n.refCount++,t!=null&&oo(t)));break;case 12:if(c&2048){Ti(t,n,a,o),t=n.stateNode;try{var f=n.memoizedProps,_=f.id,R=f.onPostCommit;typeof R=="function"&&R(_,n.alternate===null?"mount":"update",t.passiveEffectDuration,-0)}catch(P){Bt(n,n.return,P)}}else Ti(t,n,a,o);break;case 31:Ti(t,n,a,o);break;case 13:Ti(t,n,a,o);break;case 23:break;case 22:f=n.stateNode,_=n.alternate,n.memoizedState!==null?f._visibility&2?Ti(t,n,a,o):Eo(t,n):f._visibility&2?Ti(t,n,a,o):(f._visibility|=2,cr(t,n,a,o,(n.subtreeFlags&10256)!==0||!1)),c&2048&&Tf(_,n);break;case 24:Ti(t,n,a,o),c&2048&&Af(n.alternate,n);break;default:Ti(t,n,a,o)}}function cr(t,n,a,o,c){for(c=c&&((n.subtreeFlags&10256)!==0||!1),n=n.child;n!==null;){var f=t,_=n,R=a,P=o,J=_.flags;switch(_.tag){case 0:case 11:case 15:cr(f,_,R,P,c),Mo(8,_);break;case 23:break;case 22:var fe=_.stateNode;_.memoizedState!==null?fe._visibility&2?cr(f,_,R,P,c):Eo(f,_):(fe._visibility|=2,cr(f,_,R,P,c)),c&&J&2048&&Tf(_.alternate,_);break;case 24:cr(f,_,R,P,c),c&&J&2048&&Af(_.alternate,_);break;default:cr(f,_,R,P,c)}n=n.sibling}}function Eo(t,n){if(n.subtreeFlags&10256)for(n=n.child;n!==null;){var a=t,o=n,c=o.flags;switch(o.tag){case 22:Eo(a,o),c&2048&&Tf(o.alternate,o);break;case 24:Eo(a,o),c&2048&&Af(o.alternate,o);break;default:Eo(a,o)}n=n.sibling}}var bo=8192;function ur(t,n,a){if(t.subtreeFlags&bo)for(t=t.child;t!==null;)Mg(t,n,a),t=t.sibling}function Mg(t,n,a){switch(t.tag){case 26:ur(t,n,a),t.flags&bo&&t.memoizedState!==null&&LS(a,bi,t.memoizedState,t.memoizedProps);break;case 5:ur(t,n,a);break;case 3:case 4:var o=bi;bi=nc(t.stateNode.containerInfo),ur(t,n,a),bi=o;break;case 22:t.memoizedState===null&&(o=t.alternate,o!==null&&o.memoizedState!==null?(o=bo,bo=16777216,ur(t,n,a),bo=o):ur(t,n,a));break;default:ur(t,n,a)}}function yg(t){var n=t.alternate;if(n!==null&&(t=n.child,t!==null)){n.child=null;do n=t.sibling,t.sibling=null,t=n;while(t!==null)}}function To(t){var n=t.deletions;if((t.flags&16)!==0){if(n!==null)for(var a=0;a<n.length;a++){var o=n[a];En=o,bg(o,t)}yg(t)}if(t.subtreeFlags&10256)for(t=t.child;t!==null;)Eg(t),t=t.sibling}function Eg(t){switch(t.tag){case 0:case 11:case 15:To(t),t.flags&2048&&Ha(9,t,t.return);break;case 3:To(t);break;case 12:To(t);break;case 22:var n=t.stateNode;t.memoizedState!==null&&n._visibility&2&&(t.return===null||t.return.tag!==13)?(n._visibility&=-3,kl(t)):To(t);break;default:To(t)}}function kl(t){var n=t.deletions;if((t.flags&16)!==0){if(n!==null)for(var a=0;a<n.length;a++){var o=n[a];En=o,bg(o,t)}yg(t)}for(t=t.child;t!==null;){switch(n=t,n.tag){case 0:case 11:case 15:Ha(8,n,n.return),kl(n);break;case 22:a=n.stateNode,a._visibility&2&&(a._visibility&=-3,kl(n));break;default:kl(n)}t=t.sibling}}function bg(t,n){for(;En!==null;){var a=En;switch(a.tag){case 0:case 11:case 15:Ha(8,a,n);break;case 23:case 22:if(a.memoizedState!==null&&a.memoizedState.cachePool!==null){var o=a.memoizedState.cachePool.pool;o!=null&&o.refCount++}break;case 24:oo(a.memoizedState.cache)}if(o=a.child,o!==null)o.return=a,En=o;else e:for(a=t;En!==null;){o=En;var c=o.sibling,f=o.return;if(pg(o),o===a){En=null;break e}if(c!==null){c.return=f,En=c;break e}En=f}}}var Yx={getCacheForType:function(t){var n=Rn(pn),a=n.data.get(t);return a===void 0&&(a=t(),n.data.set(t,a)),a},cacheSignal:function(){return Rn(pn).controller.signal}},Zx=typeof WeakMap=="function"?WeakMap:Map,Dt=0,Wt=null,ht=null,gt=0,Ft=0,ii=null,Ga=!1,fr=!1,Rf=!1,fa=0,rn=0,Va=0,Cs=0,Cf=0,ai=0,dr=0,Ao=null,Wn=null,wf=!1,Xl=0,Tg=0,Wl=1/0,ql=null,ka=null,Sn=0,Xa=null,hr=null,da=0,Df=0,Uf=null,Ag=null,Ro=0,Lf=null;function si(){return(Dt&2)!==0&&gt!==0?gt&-gt:N.T!==null?Bf():Kr()}function Rg(){if(ai===0)if((gt&536870912)===0||Mt){var t=it;it<<=1,(it&3932160)===0&&(it=262144),ai=t}else ai=536870912;return t=ti.current,t!==null&&(t.flags|=32),ai}function qn(t,n,a){(t===Wt&&(Ft===2||Ft===9)||t.cancelPendingCommit!==null)&&(pr(t,0),Wa(t,gt,ai,!1)),Ie(t,a),((Dt&2)===0||t!==Wt)&&(t===Wt&&((Dt&2)===0&&(Cs|=a),rn===4&&Wa(t,gt,ai,!1)),Bi(t))}function Cg(t,n,a){if((Dt&6)!==0)throw Error(r(327));var o=!a&&(n&127)===0&&(n&t.expiredLanes)===0||Re(t,n),c=o?Jx(t,n):Of(t,n,!0),f=o;do{if(c===0){fr&&!o&&Wa(t,n,0,!1);break}else{if(a=t.current.alternate,f&&!Kx(a)){c=Of(t,n,!1),f=!1;continue}if(c===2){if(f=n,t.errorRecoveryDisabledLanes&f)var _=0;else _=t.pendingLanes&-536870913,_=_!==0?_:_&536870912?536870912:0;if(_!==0){n=_;e:{var R=t;c=Ao;var P=R.current.memoizedState.isDehydrated;if(P&&(pr(R,_).flags|=256),_=Of(R,_,!1),_!==2){if(Rf&&!P){R.errorRecoveryDisabledLanes|=f,Cs|=f,c=4;break e}f=Wn,Wn=c,f!==null&&(Wn===null?Wn=f:Wn.push.apply(Wn,f))}c=_}if(f=!1,c!==2)continue}}if(c===1){pr(t,0),Wa(t,n,0,!0);break}e:{switch(o=t,f=c,f){case 0:case 1:throw Error(r(345));case 4:if((n&4194048)!==n)break;case 6:Wa(o,n,ai,!Ga);break e;case 2:Wn=null;break;case 3:case 5:break;default:throw Error(r(329))}if((n&62914560)===n&&(c=Xl+300-Ht(),10<c)){if(Wa(o,n,ai,!Ga),me(o,0,!0)!==0)break e;da=n,o.timeoutHandle=s0(wg.bind(null,o,a,Wn,ql,wf,n,ai,Cs,dr,Ga,f,"Throttled",-0,0),c);break e}wg(o,a,Wn,ql,wf,n,ai,Cs,dr,Ga,f,null,-0,0)}}break}while(!0);Bi(t)}function wg(t,n,a,o,c,f,_,R,P,J,fe,_e,ie,re){if(t.timeoutHandle=-1,_e=n.subtreeFlags,_e&8192||(_e&16785408)===16785408){_e={stylesheets:null,count:0,imgCount:0,imgBytes:0,suspenseyImages:[],waitingForImages:!0,waitingForViewTransition:!1,unsuspend:Ji},Mg(n,f,_e);var Ve=(f&62914560)===f?Xl-Ht():(f&4194048)===f?Tg-Ht():0;if(Ve=NS(_e,Ve),Ve!==null){da=f,t.cancelPendingCommit=Ve(Fg.bind(null,t,n,f,a,o,c,_,R,P,fe,_e,null,ie,re)),Wa(t,f,_,!J);return}}Fg(t,n,f,a,o,c,_,R,P)}function Kx(t){for(var n=t;;){var a=n.tag;if((a===0||a===11||a===15)&&n.flags&16384&&(a=n.updateQueue,a!==null&&(a=a.stores,a!==null)))for(var o=0;o<a.length;o++){var c=a[o],f=c.getSnapshot;c=c.value;try{if(!jn(f(),c))return!1}catch{return!1}}if(a=n.child,n.subtreeFlags&16384&&a!==null)a.return=n,n=a;else{if(n===t)break;for(;n.sibling===null;){if(n.return===null||n.return===t)return!0;n=n.return}n.sibling.return=n.return,n=n.sibling}}return!0}function Wa(t,n,a,o){n&=~Cf,n&=~Cs,t.suspendedLanes|=n,t.pingedLanes&=~n,o&&(t.warmLanes|=n),o=t.expirationTimes;for(var c=n;0<c;){var f=31-Pe(c),_=1<<f;o[f]=-1,c&=~_}a!==0&&Nt(t,a,n)}function Yl(){return(Dt&6)===0?(Co(0),!1):!0}function Nf(){if(ht!==null){if(Ft===0)var t=ht.return;else t=ht,ta=xs=null,Zu(t),ar=null,co=0,t=ht;for(;t!==null;)rg(t.alternate,t),t=t.return;ht=null}}function pr(t,n){var a=t.timeoutHandle;a!==-1&&(t.timeoutHandle=-1,mS(a)),a=t.cancelPendingCommit,a!==null&&(t.cancelPendingCommit=null,a()),da=0,Nf(),Wt=t,ht=a=ji(t.current,null),gt=n,Ft=0,ii=null,Ga=!1,fr=Re(t,n),Rf=!1,dr=ai=Cf=Cs=Va=rn=0,Wn=Ao=null,wf=!1,(n&8)!==0&&(n|=n&32);var o=t.entangledLanes;if(o!==0)for(t=t.entanglements,o&=n;0<o;){var c=31-Pe(o),f=1<<c;n|=t[c],o&=~f}return fa=n,pl(),a}function Dg(t,n){ot=null,N.H=vo,n===ir||n===yl?(n=qp(),Ft=3):n===zu?(n=qp(),Ft=4):Ft=n===ff?8:n!==null&&typeof n=="object"&&typeof n.then=="function"?6:1,ii=n,ht===null&&(rn=1,zl(t,ci(n,t.current)))}function Ug(){var t=ti.current;return t===null?!0:(gt&4194048)===gt?hi===null:(gt&62914560)===gt||(gt&536870912)!==0?t===hi:!1}function Lg(){var t=N.H;return N.H=vo,t===null?vo:t}function Ng(){var t=N.A;return N.A=Yx,t}function Zl(){rn=4,Ga||(gt&4194048)!==gt&&ti.current!==null||(fr=!0),(Va&134217727)===0&&(Cs&134217727)===0||Wt===null||Wa(Wt,gt,ai,!1)}function Of(t,n,a){var o=Dt;Dt|=2;var c=Lg(),f=Ng();(Wt!==t||gt!==n)&&(ql=null,pr(t,n)),n=!1;var _=rn;e:do try{if(Ft!==0&&ht!==null){var R=ht,P=ii;switch(Ft){case 8:Nf(),_=6;break e;case 3:case 2:case 9:case 6:ti.current===null&&(n=!0);var J=Ft;if(Ft=0,ii=null,mr(t,R,P,J),a&&fr){_=0;break e}break;default:J=Ft,Ft=0,ii=null,mr(t,R,P,J)}}Qx(),_=rn;break}catch(fe){Dg(t,fe)}while(!0);return n&&t.shellSuspendCounter++,ta=xs=null,Dt=o,N.H=c,N.A=f,ht===null&&(Wt=null,gt=0,pl()),_}function Qx(){for(;ht!==null;)Og(ht)}function Jx(t,n){var a=Dt;Dt|=2;var o=Lg(),c=Ng();Wt!==t||gt!==n?(ql=null,Wl=Ht()+500,pr(t,n)):fr=Re(t,n);e:do try{if(Ft!==0&&ht!==null){n=ht;var f=ii;t:switch(Ft){case 1:Ft=0,ii=null,mr(t,n,f,1);break;case 2:case 9:if(Xp(f)){Ft=0,ii=null,Pg(n);break}n=function(){Ft!==2&&Ft!==9||Wt!==t||(Ft=7),Bi(t)},f.then(n,n);break e;case 3:Ft=7;break e;case 4:Ft=5;break e;case 7:Xp(f)?(Ft=0,ii=null,Pg(n)):(Ft=0,ii=null,mr(t,n,f,7));break;case 5:var _=null;switch(ht.tag){case 26:_=ht.memoizedState;case 5:case 27:var R=ht;if(_?S0(_):R.stateNode.complete){Ft=0,ii=null;var P=R.sibling;if(P!==null)ht=P;else{var J=R.return;J!==null?(ht=J,Kl(J)):ht=null}break t}}Ft=0,ii=null,mr(t,n,f,5);break;case 6:Ft=0,ii=null,mr(t,n,f,6);break;case 8:Nf(),rn=6;break e;default:throw Error(r(462))}}$x();break}catch(fe){Dg(t,fe)}while(!0);return ta=xs=null,N.H=o,N.A=c,Dt=a,ht!==null?0:(Wt=null,gt=0,pl(),rn)}function $x(){for(;ht!==null&&!an();)Og(ht)}function Og(t){var n=ag(t.alternate,t,fa);t.memoizedProps=t.pendingProps,n===null?Kl(t):ht=n}function Pg(t){var n=t,a=n.alternate;switch(n.tag){case 15:case 0:n=$m(a,n,n.pendingProps,n.type,void 0,gt);break;case 11:n=$m(a,n,n.pendingProps,n.type.render,n.ref,gt);break;case 5:Zu(n);default:rg(a,n),n=ht=Np(n,fa),n=ag(a,n,fa)}t.memoizedProps=t.pendingProps,n===null?Kl(t):ht=n}function mr(t,n,a,o){ta=xs=null,Zu(n),ar=null,co=0;var c=n.return;try{if(Hx(t,c,n,a,gt)){rn=1,zl(t,ci(a,t.current)),ht=null;return}}catch(f){if(c!==null)throw ht=c,f;rn=1,zl(t,ci(a,t.current)),ht=null;return}n.flags&32768?(Mt||o===1?t=!0:fr||(gt&536870912)!==0?t=!1:(Ga=t=!0,(o===2||o===9||o===3||o===6)&&(o=ti.current,o!==null&&o.tag===13&&(o.flags|=16384))),zg(n,t)):Kl(n)}function Kl(t){var n=t;do{if((n.flags&32768)!==0){zg(n,Ga);return}t=n.return;var a=kx(n.alternate,n,fa);if(a!==null){ht=a;return}if(n=n.sibling,n!==null){ht=n;return}ht=n=t}while(n!==null);rn===0&&(rn=5)}function zg(t,n){do{var a=Xx(t.alternate,t);if(a!==null){a.flags&=32767,ht=a;return}if(a=t.return,a!==null&&(a.flags|=32768,a.subtreeFlags=0,a.deletions=null),!n&&(t=t.sibling,t!==null)){ht=t;return}ht=t=a}while(t!==null);rn=6,ht=null}function Fg(t,n,a,o,c,f,_,R,P){t.cancelPendingCommit=null;do Ql();while(Sn!==0);if((Dt&6)!==0)throw Error(r(327));if(n!==null){if(n===t.current)throw Error(r(177));if(f=n.lanes|n.childLanes,f|=Mu,Qt(t,a,f,_,R,P),t===Wt&&(ht=Wt=null,gt=0),hr=n,Xa=t,da=a,Df=f,Uf=c,Ag=o,(n.subtreeFlags&10256)!==0||(n.flags&10256)!==0?(t.callbackNode=null,t.callbackPriority=0,nS(Y,function(){return Vg(),null})):(t.callbackNode=null,t.callbackPriority=0),o=(n.flags&13878)!==0,(n.subtreeFlags&13878)!==0||o){o=N.T,N.T=null,c=I.p,I.p=2,_=Dt,Dt|=4;try{Wx(t,n,a)}finally{Dt=_,I.p=c,N.T=o}}Sn=1,Bg(),Ig(),Hg()}}function Bg(){if(Sn===1){Sn=0;var t=Xa,n=hr,a=(n.flags&13878)!==0;if((n.subtreeFlags&13878)!==0||a){a=N.T,N.T=null;var o=I.p;I.p=2;var c=Dt;Dt|=4;try{vg(n,t);var f=qf,_=bp(t.containerInfo),R=f.focusedElem,P=f.selectionRange;if(_!==R&&R&&R.ownerDocument&&Ep(R.ownerDocument.documentElement,R)){if(P!==null&&gu(R)){var J=P.start,fe=P.end;if(fe===void 0&&(fe=J),"selectionStart"in R)R.selectionStart=J,R.selectionEnd=Math.min(fe,R.value.length);else{var _e=R.ownerDocument||document,ie=_e&&_e.defaultView||window;if(ie.getSelection){var re=ie.getSelection(),Ve=R.textContent.length,je=Math.min(P.start,Ve),kt=P.end===void 0?je:Math.min(P.end,Ve);!re.extend&&je>kt&&(_=kt,kt=je,je=_);var q=yp(R,je),H=yp(R,kt);if(q&&H&&(re.rangeCount!==1||re.anchorNode!==q.node||re.anchorOffset!==q.offset||re.focusNode!==H.node||re.focusOffset!==H.offset)){var Q=_e.createRange();Q.setStart(q.node,q.offset),re.removeAllRanges(),je>kt?(re.addRange(Q),re.extend(H.node,H.offset)):(Q.setEnd(H.node,H.offset),re.addRange(Q))}}}}for(_e=[],re=R;re=re.parentNode;)re.nodeType===1&&_e.push({element:re,left:re.scrollLeft,top:re.scrollTop});for(typeof R.focus=="function"&&R.focus(),R=0;R<_e.length;R++){var ge=_e[R];ge.element.scrollLeft=ge.left,ge.element.scrollTop=ge.top}}lc=!!Wf,qf=Wf=null}finally{Dt=c,I.p=o,N.T=a}}t.current=n,Sn=2}}function Ig(){if(Sn===2){Sn=0;var t=Xa,n=hr,a=(n.flags&8772)!==0;if((n.subtreeFlags&8772)!==0||a){a=N.T,N.T=null;var o=I.p;I.p=2;var c=Dt;Dt|=4;try{hg(t,n.alternate,n)}finally{Dt=c,I.p=o,N.T=a}}Sn=3}}function Hg(){if(Sn===4||Sn===3){Sn=0,X();var t=Xa,n=hr,a=da,o=Ag;(n.subtreeFlags&10256)!==0||(n.flags&10256)!==0?Sn=5:(Sn=0,hr=Xa=null,Gg(t,t.pendingLanes));var c=t.pendingLanes;if(c===0&&(ka=null),Zr(a),n=n.stateNode,ce&&typeof ce.onCommitFiberRoot=="function")try{ce.onCommitFiberRoot(oe,n,void 0,(n.current.flags&128)===128)}catch{}if(o!==null){n=N.T,c=I.p,I.p=2,N.T=null;try{for(var f=t.onRecoverableError,_=0;_<o.length;_++){var R=o[_];f(R.value,{componentStack:R.stack})}}finally{N.T=n,I.p=c}}(da&3)!==0&&Ql(),Bi(t),c=t.pendingLanes,(a&261930)!==0&&(c&42)!==0?t===Lf?Ro++:(Ro=0,Lf=t):Ro=0,Co(0)}}function Gg(t,n){(t.pooledCacheLanes&=n)===0&&(n=t.pooledCache,n!=null&&(t.pooledCache=null,oo(n)))}function Ql(){return Bg(),Ig(),Hg(),Vg()}function Vg(){if(Sn!==5)return!1;var t=Xa,n=Df;Df=0;var a=Zr(da),o=N.T,c=I.p;try{I.p=32>a?32:a,N.T=null,a=Uf,Uf=null;var f=Xa,_=da;if(Sn=0,hr=Xa=null,da=0,(Dt&6)!==0)throw Error(r(331));var R=Dt;if(Dt|=4,Eg(f.current),Sg(f,f.current,_,a),Dt=R,Co(0,!1),ce&&typeof ce.onPostCommitFiberRoot=="function")try{ce.onPostCommitFiberRoot(oe,f)}catch{}return!0}finally{I.p=c,N.T=o,Gg(t,n)}}function kg(t,n,a){n=ci(a,n),n=uf(t.stateNode,n,2),t=Fa(t,n,2),t!==null&&(Ie(t,2),Bi(t))}function Bt(t,n,a){if(t.tag===3)kg(t,t,a);else for(;n!==null;){if(n.tag===3){kg(n,t,a);break}else if(n.tag===1){var o=n.stateNode;if(typeof n.type.getDerivedStateFromError=="function"||typeof o.componentDidCatch=="function"&&(ka===null||!ka.has(o))){t=ci(a,t),a=Xm(2),o=Fa(n,a,2),o!==null&&(Wm(a,o,n,t),Ie(o,2),Bi(o));break}}n=n.return}}function Pf(t,n,a){var o=t.pingCache;if(o===null){o=t.pingCache=new Zx;var c=new Set;o.set(n,c)}else c=o.get(n),c===void 0&&(c=new Set,o.set(n,c));c.has(a)||(Rf=!0,c.add(a),t=jx.bind(null,t,n,a),n.then(t,t))}function jx(t,n,a){var o=t.pingCache;o!==null&&o.delete(n),t.pingedLanes|=t.suspendedLanes&a,t.warmLanes&=~a,Wt===t&&(gt&a)===a&&(rn===4||rn===3&&(gt&62914560)===gt&&300>Ht()-Xl?(Dt&2)===0&&pr(t,0):Cf|=a,dr===gt&&(dr=0)),Bi(t)}function Xg(t,n){n===0&&(n=ve()),t=gs(t,n),t!==null&&(Ie(t,n),Bi(t))}function eS(t){var n=t.memoizedState,a=0;n!==null&&(a=n.retryLane),Xg(t,a)}function tS(t,n){var a=0;switch(t.tag){case 31:case 13:var o=t.stateNode,c=t.memoizedState;c!==null&&(a=c.retryLane);break;case 19:o=t.stateNode;break;case 22:o=t.stateNode._retryCache;break;default:throw Error(r(314))}o!==null&&o.delete(n),Xg(t,a)}function nS(t,n){return ln(t,n)}var Jl=null,gr=null,zf=!1,$l=!1,Ff=!1,qa=0;function Bi(t){t!==gr&&t.next===null&&(gr===null?Jl=gr=t:gr=gr.next=t),$l=!0,zf||(zf=!0,aS())}function Co(t,n){if(!Ff&&$l){Ff=!0;do for(var a=!1,o=Jl;o!==null;){if(t!==0){var c=o.pendingLanes;if(c===0)var f=0;else{var _=o.suspendedLanes,R=o.pingedLanes;f=(1<<31-Pe(42|t)+1)-1,f&=c&~(_&~R),f=f&201326741?f&201326741|1:f?f|2:0}f!==0&&(a=!0,Zg(o,f))}else f=gt,f=me(o,o===Wt?f:0,o.cancelPendingCommit!==null||o.timeoutHandle!==-1),(f&3)===0||Re(o,f)||(a=!0,Zg(o,f));o=o.next}while(a);Ff=!1}}function iS(){Wg()}function Wg(){$l=zf=!1;var t=0;qa!==0&&pS()&&(t=qa);for(var n=Ht(),a=null,o=Jl;o!==null;){var c=o.next,f=qg(o,n);f===0?(o.next=null,a===null?Jl=c:a.next=c,c===null&&(gr=a)):(a=o,(t!==0||(f&3)!==0)&&($l=!0)),o=c}Sn!==0&&Sn!==5||Co(t),qa!==0&&(qa=0)}function qg(t,n){for(var a=t.suspendedLanes,o=t.pingedLanes,c=t.expirationTimes,f=t.pendingLanes&-62914561;0<f;){var _=31-Pe(f),R=1<<_,P=c[_];P===-1?((R&a)===0||(R&o)!==0)&&(c[_]=Oe(R,n)):P<=n&&(t.expiredLanes|=R),f&=~R}if(n=Wt,a=gt,a=me(t,t===n?a:0,t.cancelPendingCommit!==null||t.timeoutHandle!==-1),o=t.callbackNode,a===0||t===n&&(Ft===2||Ft===9)||t.cancelPendingCommit!==null)return o!==null&&o!==null&&qt(o),t.callbackNode=null,t.callbackPriority=0;if((a&3)===0||Re(t,a)){if(n=a&-a,n===t.callbackPriority)return n;switch(o!==null&&qt(o),Zr(a)){case 2:case 8:a=M;break;case 32:a=Y;break;case 268435456:a=le;break;default:a=Y}return o=Yg.bind(null,t),a=ln(a,o),t.callbackPriority=n,t.callbackNode=a,n}return o!==null&&o!==null&&qt(o),t.callbackPriority=2,t.callbackNode=null,2}function Yg(t,n){if(Sn!==0&&Sn!==5)return t.callbackNode=null,t.callbackPriority=0,null;var a=t.callbackNode;if(Ql()&&t.callbackNode!==a)return null;var o=gt;return o=me(t,t===Wt?o:0,t.cancelPendingCommit!==null||t.timeoutHandle!==-1),o===0?null:(Cg(t,o,n),qg(t,Ht()),t.callbackNode!=null&&t.callbackNode===a?Yg.bind(null,t):null)}function Zg(t,n){if(Ql())return null;Cg(t,n,!0)}function aS(){gS(function(){(Dt&6)!==0?ln(w,iS):Wg()})}function Bf(){if(qa===0){var t=tr;t===0&&(t=Ke,Ke<<=1,(Ke&261888)===0&&(Ke=256)),qa=t}return qa}function Kg(t){return t==null||typeof t=="symbol"||typeof t=="boolean"?null:typeof t=="function"?t:ds(""+t)}function Qg(t,n){var a=n.ownerDocument.createElement("input");return a.name=n.name,a.value=n.value,t.id&&a.setAttribute("form",t.id),n.parentNode.insertBefore(a,n),t=new FormData(t),a.parentNode.removeChild(a),t}function sS(t,n,a,o,c){if(n==="submit"&&a&&a.stateNode===c){var f=Kg((c[Tn]||null).action),_=o.submitter;_&&(n=(n=_[Tn]||null)?Kg(n.formAction):_.getAttribute("formAction"),n!==null&&(f=n,_=null));var R=new ul("action","action",null,o,c);t.push({event:R,listeners:[{instance:null,listener:function(){if(o.defaultPrevented){if(qa!==0){var P=_?Qg(c,_):new FormData(c);af(a,{pending:!0,data:P,method:c.method,action:f},null,P)}}else typeof f=="function"&&(R.preventDefault(),P=_?Qg(c,_):new FormData(c),af(a,{pending:!0,data:P,method:c.method,action:f},f,P))},currentTarget:c}]})}}for(var If=0;If<Su.length;If++){var Hf=Su[If],rS=Hf.toLowerCase(),oS=Hf[0].toUpperCase()+Hf.slice(1);Ei(rS,"on"+oS)}Ei(Rp,"onAnimationEnd"),Ei(Cp,"onAnimationIteration"),Ei(wp,"onAnimationStart"),Ei("dblclick","onDoubleClick"),Ei("focusin","onFocus"),Ei("focusout","onBlur"),Ei(Ex,"onTransitionRun"),Ei(bx,"onTransitionStart"),Ei(Tx,"onTransitionCancel"),Ei(Dp,"onTransitionEnd"),ne("onMouseEnter",["mouseout","mouseover"]),ne("onMouseLeave",["mouseout","mouseover"]),ne("onPointerEnter",["pointerout","pointerover"]),ne("onPointerLeave",["pointerout","pointerover"]),k("onChange","change click focusin focusout input keydown keyup selectionchange".split(" ")),k("onSelect","focusout contextmenu dragend focusin keydown keyup mousedown mouseup selectionchange".split(" ")),k("onBeforeInput",["compositionend","keypress","textInput","paste"]),k("onCompositionEnd","compositionend focusout keydown keypress keyup mousedown".split(" ")),k("onCompositionStart","compositionstart focusout keydown keypress keyup mousedown".split(" ")),k("onCompositionUpdate","compositionupdate focusout keydown keypress keyup mousedown".split(" "));var wo="abort canplay canplaythrough durationchange emptied encrypted ended error loadeddata loadedmetadata loadstart pause play playing progress ratechange resize seeked seeking stalled suspend timeupdate volumechange waiting".split(" "),lS=new Set("beforetoggle cancel close invalid load scroll scrollend toggle".split(" ").concat(wo));function Jg(t,n){n=(n&4)!==0;for(var a=0;a<t.length;a++){var o=t[a],c=o.event;o=o.listeners;e:{var f=void 0;if(n)for(var _=o.length-1;0<=_;_--){var R=o[_],P=R.instance,J=R.currentTarget;if(R=R.listener,P!==f&&c.isPropagationStopped())break e;f=R,c.currentTarget=J;try{f(c)}catch(fe){hl(fe)}c.currentTarget=null,f=P}else for(_=0;_<o.length;_++){if(R=o[_],P=R.instance,J=R.currentTarget,R=R.listener,P!==f&&c.isPropagationStopped())break e;f=R,c.currentTarget=J;try{f(c)}catch(fe){hl(fe)}c.currentTarget=null,f=P}}}}function pt(t,n){var a=n[cs];a===void 0&&(a=n[cs]=new Set);var o=t+"__bubble";a.has(o)||($g(n,t,2,!1),a.add(o))}function Gf(t,n,a){var o=0;n&&(o|=4),$g(a,t,o,n)}var jl="_reactListening"+Math.random().toString(36).slice(2);function Vf(t){if(!t[jl]){t[jl]=!0,rl.forEach(function(a){a!=="selectionchange"&&(lS.has(a)||Gf(a,!1,t),Gf(a,!0,t))});var n=t.nodeType===9?t:t.ownerDocument;n===null||n[jl]||(n[jl]=!0,Gf("selectionchange",!1,n))}}function $g(t,n,a,o){switch(R0(n)){case 2:var c=zS;break;case 8:c=FS;break;default:c=id}a=c.bind(null,n,a,t),c=void 0,!ou||n!=="touchstart"&&n!=="touchmove"&&n!=="wheel"||(c=!0),o?c!==void 0?t.addEventListener(n,a,{capture:!0,passive:c}):t.addEventListener(n,a,!0):c!==void 0?t.addEventListener(n,a,{passive:c}):t.addEventListener(n,a,!1)}function kf(t,n,a,o,c){var f=o;if((n&1)===0&&(n&2)===0&&o!==null)e:for(;;){if(o===null)return;var _=o.tag;if(_===3||_===4){var R=o.stateNode.containerInfo;if(R===c)break;if(_===4)for(_=o.return;_!==null;){var P=_.tag;if((P===3||P===4)&&_.stateNode.containerInfo===c)return;_=_.return}for(;R!==null;){if(_=Ki(R),_===null)return;if(P=_.tag,P===5||P===6||P===26||P===27){o=f=_;continue e}R=R.parentNode}}o=o.return}ip(function(){var J=f,fe=su(a),_e=[];e:{var ie=Up.get(t);if(ie!==void 0){var re=ul,Ve=t;switch(t){case"keypress":if(ll(a)===0)break e;case"keydown":case"keyup":re=tx;break;case"focusin":Ve="focus",re=fu;break;case"focusout":Ve="blur",re=fu;break;case"beforeblur":case"afterblur":re=fu;break;case"click":if(a.button===2)break e;case"auxclick":case"dblclick":case"mousedown":case"mousemove":case"mouseup":case"mouseout":case"mouseover":case"contextmenu":re=rp;break;case"drag":case"dragend":case"dragenter":case"dragexit":case"dragleave":case"dragover":case"dragstart":case"drop":re=kv;break;case"touchcancel":case"touchend":case"touchmove":case"touchstart":re=ax;break;case Rp:case Cp:case wp:re=qv;break;case Dp:re=rx;break;case"scroll":case"scrollend":re=Gv;break;case"wheel":re=lx;break;case"copy":case"cut":case"paste":re=Zv;break;case"gotpointercapture":case"lostpointercapture":case"pointercancel":case"pointerdown":case"pointermove":case"pointerout":case"pointerover":case"pointerup":re=lp;break;case"toggle":case"beforetoggle":re=ux}var je=(n&4)!==0,kt=!je&&(t==="scroll"||t==="scrollend"),q=je?ie!==null?ie+"Capture":null:ie;je=[];for(var H=J,Q;H!==null;){var ge=H;if(Q=ge.stateNode,ge=ge.tag,ge!==5&&ge!==26&&ge!==27||Q===null||q===null||(ge=Qr(H,q),ge!=null&&je.push(Do(H,ge,Q))),kt)break;H=H.return}0<je.length&&(ie=new re(ie,Ve,null,a,fe),_e.push({event:ie,listeners:je}))}}if((n&7)===0){e:{if(ie=t==="mouseover"||t==="pointerover",re=t==="mouseout"||t==="pointerout",ie&&a!==au&&(Ve=a.relatedTarget||a.fromElement)&&(Ki(Ve)||Ve[Hn]))break e;if((re||ie)&&(ie=fe.window===fe?fe:(ie=fe.ownerDocument)?ie.defaultView||ie.parentWindow:window,re?(Ve=a.relatedTarget||a.toElement,re=J,Ve=Ve?Ki(Ve):null,Ve!==null&&(kt=u(Ve),je=Ve.tag,Ve!==kt||je!==5&&je!==27&&je!==6)&&(Ve=null)):(re=null,Ve=J),re!==Ve)){if(je=rp,ge="onMouseLeave",q="onMouseEnter",H="mouse",(t==="pointerout"||t==="pointerover")&&(je=lp,ge="onPointerLeave",q="onPointerEnter",H="pointer"),kt=re==null?ie:fs(re),Q=Ve==null?ie:fs(Ve),ie=new je(ge,H+"leave",re,a,fe),ie.target=kt,ie.relatedTarget=Q,ge=null,Ki(fe)===J&&(je=new je(q,H+"enter",Ve,a,fe),je.target=Q,je.relatedTarget=kt,ge=je),kt=ge,re&&Ve)t:{for(je=cS,q=re,H=Ve,Q=0,ge=q;ge;ge=je(ge))Q++;ge=0;for(var Je=H;Je;Je=je(Je))ge++;for(;0<Q-ge;)q=je(q),Q--;for(;0<ge-Q;)H=je(H),ge--;for(;Q--;){if(q===H||H!==null&&q===H.alternate){je=q;break t}q=je(q),H=je(H)}je=null}else je=null;re!==null&&jg(_e,ie,re,je,!1),Ve!==null&&kt!==null&&jg(_e,kt,Ve,je,!0)}}e:{if(ie=J?fs(J):window,re=ie.nodeName&&ie.nodeName.toLowerCase(),re==="select"||re==="input"&&ie.type==="file")var At=gp;else if(pp(ie))if(_p)At=Sx;else{At=vx;var Xe=_x}else re=ie.nodeName,!re||re.toLowerCase()!=="input"||ie.type!=="checkbox"&&ie.type!=="radio"?J&&Lt(J.elementType)&&(At=gp):At=xx;if(At&&(At=At(t,J))){mp(_e,At,a,fe);break e}Xe&&Xe(t,ie,J),t==="focusout"&&J&&ie.type==="number"&&J.memoizedProps.value!=null&&dt(ie,"number",ie.value)}switch(Xe=J?fs(J):window,t){case"focusin":(pp(Xe)||Xe.contentEditable==="true")&&(Ys=Xe,_u=J,ao=null);break;case"focusout":ao=_u=Ys=null;break;case"mousedown":vu=!0;break;case"contextmenu":case"mouseup":case"dragend":vu=!1,Tp(_e,a,fe);break;case"selectionchange":if(yx)break;case"keydown":case"keyup":Tp(_e,a,fe)}var lt;if(hu)e:{switch(t){case"compositionstart":var _t="onCompositionStart";break e;case"compositionend":_t="onCompositionEnd";break e;case"compositionupdate":_t="onCompositionUpdate";break e}_t=void 0}else qs?dp(t,a)&&(_t="onCompositionEnd"):t==="keydown"&&a.keyCode===229&&(_t="onCompositionStart");_t&&(cp&&a.locale!=="ko"&&(qs||_t!=="onCompositionStart"?_t==="onCompositionEnd"&&qs&&(lt=ap()):(Da=fe,lu="value"in Da?Da.value:Da.textContent,qs=!0)),Xe=ec(J,_t),0<Xe.length&&(_t=new op(_t,t,null,a,fe),_e.push({event:_t,listeners:Xe}),lt?_t.data=lt:(lt=hp(a),lt!==null&&(_t.data=lt)))),(lt=dx?hx(t,a):px(t,a))&&(_t=ec(J,"onBeforeInput"),0<_t.length&&(Xe=new op("onBeforeInput","beforeinput",null,a,fe),_e.push({event:Xe,listeners:_t}),Xe.data=lt)),sS(_e,t,J,a,fe)}Jg(_e,n)})}function Do(t,n,a){return{instance:t,listener:n,currentTarget:a}}function ec(t,n){for(var a=n+"Capture",o=[];t!==null;){var c=t,f=c.stateNode;if(c=c.tag,c!==5&&c!==26&&c!==27||f===null||(c=Qr(t,a),c!=null&&o.unshift(Do(t,c,f)),c=Qr(t,n),c!=null&&o.push(Do(t,c,f))),t.tag===3)return o;t=t.return}return[]}function cS(t){if(t===null)return null;do t=t.return;while(t&&t.tag!==5&&t.tag!==27);return t||null}function jg(t,n,a,o,c){for(var f=n._reactName,_=[];a!==null&&a!==o;){var R=a,P=R.alternate,J=R.stateNode;if(R=R.tag,P!==null&&P===o)break;R!==5&&R!==26&&R!==27||J===null||(P=J,c?(J=Qr(a,f),J!=null&&_.unshift(Do(a,J,P))):c||(J=Qr(a,f),J!=null&&_.push(Do(a,J,P)))),a=a.return}_.length!==0&&t.push({event:n,listeners:_})}var uS=/\r\n?/g,fS=/\u0000|\uFFFD/g;function e0(t){return(typeof t=="string"?t:""+t).replace(uS,`
`).replace(fS,"")}function t0(t,n){return n=e0(n),e0(t)===n}function Vt(t,n,a,o,c,f){switch(a){case"children":typeof o=="string"?n==="body"||n==="textarea"&&o===""||$n(t,o):(typeof o=="number"||typeof o=="bigint")&&n!=="body"&&$n(t,""+o);break;case"className":Ge(t,"class",o);break;case"tabIndex":Ge(t,"tabindex",o);break;case"dir":case"role":case"viewBox":case"width":case"height":Ge(t,a,o);break;case"style":yi(t,o,f);break;case"data":if(n!=="object"){Ge(t,"data",o);break}case"src":case"href":if(o===""&&(n!=="a"||a!=="href")){t.removeAttribute(a);break}if(o==null||typeof o=="function"||typeof o=="symbol"||typeof o=="boolean"){t.removeAttribute(a);break}o=ds(""+o),t.setAttribute(a,o);break;case"action":case"formAction":if(typeof o=="function"){t.setAttribute(a,"javascript:throw new Error('A React form was unexpectedly submitted. If you called form.submit() manually, consider using form.requestSubmit() instead. If you\\'re trying to use event.stopPropagation() in a submit event handler, consider also calling event.preventDefault().')");break}else typeof f=="function"&&(a==="formAction"?(n!=="input"&&Vt(t,n,"name",c.name,c,null),Vt(t,n,"formEncType",c.formEncType,c,null),Vt(t,n,"formMethod",c.formMethod,c,null),Vt(t,n,"formTarget",c.formTarget,c,null)):(Vt(t,n,"encType",c.encType,c,null),Vt(t,n,"method",c.method,c,null),Vt(t,n,"target",c.target,c,null)));if(o==null||typeof o=="symbol"||typeof o=="boolean"){t.removeAttribute(a);break}o=ds(""+o),t.setAttribute(a,o);break;case"onClick":o!=null&&(t.onclick=Ji);break;case"onScroll":o!=null&&pt("scroll",t);break;case"onScrollEnd":o!=null&&pt("scrollend",t);break;case"dangerouslySetInnerHTML":if(o!=null){if(typeof o!="object"||!("__html"in o))throw Error(r(61));if(a=o.__html,a!=null){if(c.children!=null)throw Error(r(60));t.innerHTML=a}}break;case"multiple":t.multiple=o&&typeof o!="function"&&typeof o!="symbol";break;case"muted":t.muted=o&&typeof o!="function"&&typeof o!="symbol";break;case"suppressContentEditableWarning":case"suppressHydrationWarning":case"defaultValue":case"defaultChecked":case"innerHTML":case"ref":break;case"autoFocus":break;case"xlinkHref":if(o==null||typeof o=="function"||typeof o=="boolean"||typeof o=="symbol"){t.removeAttribute("xlink:href");break}a=ds(""+o),t.setAttributeNS("http://www.w3.org/1999/xlink","xlink:href",a);break;case"contentEditable":case"spellCheck":case"draggable":case"value":case"autoReverse":case"externalResourcesRequired":case"focusable":case"preserveAlpha":o!=null&&typeof o!="function"&&typeof o!="symbol"?t.setAttribute(a,""+o):t.removeAttribute(a);break;case"inert":case"allowFullScreen":case"async":case"autoPlay":case"controls":case"default":case"defer":case"disabled":case"disablePictureInPicture":case"disableRemotePlayback":case"formNoValidate":case"hidden":case"loop":case"noModule":case"noValidate":case"open":case"playsInline":case"readOnly":case"required":case"reversed":case"scoped":case"seamless":case"itemScope":o&&typeof o!="function"&&typeof o!="symbol"?t.setAttribute(a,""):t.removeAttribute(a);break;case"capture":case"download":o===!0?t.setAttribute(a,""):o!==!1&&o!=null&&typeof o!="function"&&typeof o!="symbol"?t.setAttribute(a,o):t.removeAttribute(a);break;case"cols":case"rows":case"size":case"span":o!=null&&typeof o!="function"&&typeof o!="symbol"&&!isNaN(o)&&1<=o?t.setAttribute(a,o):t.removeAttribute(a);break;case"rowSpan":case"start":o==null||typeof o=="function"||typeof o=="symbol"||isNaN(o)?t.removeAttribute(a):t.setAttribute(a,o);break;case"popover":pt("beforetoggle",t),pt("toggle",t),De(t,"popover",o);break;case"xlinkActuate":He(t,"http://www.w3.org/1999/xlink","xlink:actuate",o);break;case"xlinkArcrole":He(t,"http://www.w3.org/1999/xlink","xlink:arcrole",o);break;case"xlinkRole":He(t,"http://www.w3.org/1999/xlink","xlink:role",o);break;case"xlinkShow":He(t,"http://www.w3.org/1999/xlink","xlink:show",o);break;case"xlinkTitle":He(t,"http://www.w3.org/1999/xlink","xlink:title",o);break;case"xlinkType":He(t,"http://www.w3.org/1999/xlink","xlink:type",o);break;case"xmlBase":He(t,"http://www.w3.org/XML/1998/namespace","xml:base",o);break;case"xmlLang":He(t,"http://www.w3.org/XML/1998/namespace","xml:lang",o);break;case"xmlSpace":He(t,"http://www.w3.org/XML/1998/namespace","xml:space",o);break;case"is":De(t,"is",o);break;case"innerText":case"textContent":break;default:(!(2<a.length)||a[0]!=="o"&&a[0]!=="O"||a[1]!=="n"&&a[1]!=="N")&&(a=Oi.get(a)||a,De(t,a,o))}}function Xf(t,n,a,o,c,f){switch(a){case"style":yi(t,o,f);break;case"dangerouslySetInnerHTML":if(o!=null){if(typeof o!="object"||!("__html"in o))throw Error(r(61));if(a=o.__html,a!=null){if(c.children!=null)throw Error(r(60));t.innerHTML=a}}break;case"children":typeof o=="string"?$n(t,o):(typeof o=="number"||typeof o=="bigint")&&$n(t,""+o);break;case"onScroll":o!=null&&pt("scroll",t);break;case"onScrollEnd":o!=null&&pt("scrollend",t);break;case"onClick":o!=null&&(t.onclick=Ji);break;case"suppressContentEditableWarning":case"suppressHydrationWarning":case"innerHTML":case"ref":break;case"innerText":case"textContent":break;default:if(!A.hasOwnProperty(a))e:{if(a[0]==="o"&&a[1]==="n"&&(c=a.endsWith("Capture"),n=a.slice(2,c?a.length-7:void 0),f=t[Tn]||null,f=f!=null?f[a]:null,typeof f=="function"&&t.removeEventListener(n,f,c),typeof o=="function")){typeof f!="function"&&f!==null&&(a in t?t[a]=null:t.hasAttribute(a)&&t.removeAttribute(a)),t.addEventListener(n,o,c);break e}a in t?t[a]=o:o===!0?t.setAttribute(a,""):De(t,a,o)}}}function wn(t,n,a){switch(n){case"div":case"span":case"svg":case"path":case"a":case"g":case"p":case"li":break;case"img":pt("error",t),pt("load",t);var o=!1,c=!1,f;for(f in a)if(a.hasOwnProperty(f)){var _=a[f];if(_!=null)switch(f){case"src":o=!0;break;case"srcSet":c=!0;break;case"children":case"dangerouslySetInnerHTML":throw Error(r(137,n));default:Vt(t,n,f,_,a,null)}}c&&Vt(t,n,"srcSet",a.srcSet,a,null),o&&Vt(t,n,"src",a.src,a,null);return;case"input":pt("invalid",t);var R=f=_=c=null,P=null,J=null;for(o in a)if(a.hasOwnProperty(o)){var fe=a[o];if(fe!=null)switch(o){case"name":c=fe;break;case"type":_=fe;break;case"checked":P=fe;break;case"defaultChecked":J=fe;break;case"value":f=fe;break;case"defaultValue":R=fe;break;case"children":case"dangerouslySetInnerHTML":if(fe!=null)throw Error(r(137,n));break;default:Vt(t,n,o,fe,a,null)}}Un(t,f,R,P,J,_,c,!1);return;case"select":pt("invalid",t),o=_=f=null;for(c in a)if(a.hasOwnProperty(c)&&(R=a[c],R!=null))switch(c){case"value":f=R;break;case"defaultValue":_=R;break;case"multiple":o=R;default:Vt(t,n,c,R,a,null)}n=f,a=_,t.multiple=!!o,n!=null?xn(t,!!o,n,!1):a!=null&&xn(t,!!o,a,!0);return;case"textarea":pt("invalid",t),f=c=o=null;for(_ in a)if(a.hasOwnProperty(_)&&(R=a[_],R!=null))switch(_){case"value":o=R;break;case"defaultValue":c=R;break;case"children":f=R;break;case"dangerouslySetInnerHTML":if(R!=null)throw Error(r(91));break;default:Vt(t,n,_,R,a,null)}Mi(t,o,c,f);return;case"option":for(P in a)if(a.hasOwnProperty(P)&&(o=a[P],o!=null))switch(P){case"selected":t.selected=o&&typeof o!="function"&&typeof o!="symbol";break;default:Vt(t,n,P,o,a,null)}return;case"dialog":pt("beforetoggle",t),pt("toggle",t),pt("cancel",t),pt("close",t);break;case"iframe":case"object":pt("load",t);break;case"video":case"audio":for(o=0;o<wo.length;o++)pt(wo[o],t);break;case"image":pt("error",t),pt("load",t);break;case"details":pt("toggle",t);break;case"embed":case"source":case"link":pt("error",t),pt("load",t);case"area":case"base":case"br":case"col":case"hr":case"keygen":case"meta":case"param":case"track":case"wbr":case"menuitem":for(J in a)if(a.hasOwnProperty(J)&&(o=a[J],o!=null))switch(J){case"children":case"dangerouslySetInnerHTML":throw Error(r(137,n));default:Vt(t,n,J,o,a,null)}return;default:if(Lt(n)){for(fe in a)a.hasOwnProperty(fe)&&(o=a[fe],o!==void 0&&Xf(t,n,fe,o,a,void 0));return}}for(R in a)a.hasOwnProperty(R)&&(o=a[R],o!=null&&Vt(t,n,R,o,a,null))}function dS(t,n,a,o){switch(n){case"div":case"span":case"svg":case"path":case"a":case"g":case"p":case"li":break;case"input":var c=null,f=null,_=null,R=null,P=null,J=null,fe=null;for(re in a){var _e=a[re];if(a.hasOwnProperty(re)&&_e!=null)switch(re){case"checked":break;case"value":break;case"defaultValue":P=_e;default:o.hasOwnProperty(re)||Vt(t,n,re,null,o,_e)}}for(var ie in o){var re=o[ie];if(_e=a[ie],o.hasOwnProperty(ie)&&(re!=null||_e!=null))switch(ie){case"type":f=re;break;case"name":c=re;break;case"checked":J=re;break;case"defaultChecked":fe=re;break;case"value":_=re;break;case"defaultValue":R=re;break;case"children":case"dangerouslySetInnerHTML":if(re!=null)throw Error(r(137,n));break;default:re!==_e&&Vt(t,n,ie,re,o,_e)}}ze(t,_,R,P,J,fe,f,c);return;case"select":re=_=R=ie=null;for(f in a)if(P=a[f],a.hasOwnProperty(f)&&P!=null)switch(f){case"value":break;case"multiple":re=P;default:o.hasOwnProperty(f)||Vt(t,n,f,null,o,P)}for(c in o)if(f=o[c],P=a[c],o.hasOwnProperty(c)&&(f!=null||P!=null))switch(c){case"value":ie=f;break;case"defaultValue":R=f;break;case"multiple":_=f;default:f!==P&&Vt(t,n,c,f,o,P)}n=R,a=_,o=re,ie!=null?xn(t,!!a,ie,!1):!!o!=!!a&&(n!=null?xn(t,!!a,n,!0):xn(t,!!a,a?[]:"",!1));return;case"textarea":re=ie=null;for(R in a)if(c=a[R],a.hasOwnProperty(R)&&c!=null&&!o.hasOwnProperty(R))switch(R){case"value":break;case"children":break;default:Vt(t,n,R,null,o,c)}for(_ in o)if(c=o[_],f=a[_],o.hasOwnProperty(_)&&(c!=null||f!=null))switch(_){case"value":ie=c;break;case"defaultValue":re=c;break;case"children":break;case"dangerouslySetInnerHTML":if(c!=null)throw Error(r(91));break;default:c!==f&&Vt(t,n,_,c,o,f)}Jn(t,ie,re);return;case"option":for(var Ve in a)if(ie=a[Ve],a.hasOwnProperty(Ve)&&ie!=null&&!o.hasOwnProperty(Ve))switch(Ve){case"selected":t.selected=!1;break;default:Vt(t,n,Ve,null,o,ie)}for(P in o)if(ie=o[P],re=a[P],o.hasOwnProperty(P)&&ie!==re&&(ie!=null||re!=null))switch(P){case"selected":t.selected=ie&&typeof ie!="function"&&typeof ie!="symbol";break;default:Vt(t,n,P,ie,o,re)}return;case"img":case"link":case"area":case"base":case"br":case"col":case"embed":case"hr":case"keygen":case"meta":case"param":case"source":case"track":case"wbr":case"menuitem":for(var je in a)ie=a[je],a.hasOwnProperty(je)&&ie!=null&&!o.hasOwnProperty(je)&&Vt(t,n,je,null,o,ie);for(J in o)if(ie=o[J],re=a[J],o.hasOwnProperty(J)&&ie!==re&&(ie!=null||re!=null))switch(J){case"children":case"dangerouslySetInnerHTML":if(ie!=null)throw Error(r(137,n));break;default:Vt(t,n,J,ie,o,re)}return;default:if(Lt(n)){for(var kt in a)ie=a[kt],a.hasOwnProperty(kt)&&ie!==void 0&&!o.hasOwnProperty(kt)&&Xf(t,n,kt,void 0,o,ie);for(fe in o)ie=o[fe],re=a[fe],!o.hasOwnProperty(fe)||ie===re||ie===void 0&&re===void 0||Xf(t,n,fe,ie,o,re);return}}for(var q in a)ie=a[q],a.hasOwnProperty(q)&&ie!=null&&!o.hasOwnProperty(q)&&Vt(t,n,q,null,o,ie);for(_e in o)ie=o[_e],re=a[_e],!o.hasOwnProperty(_e)||ie===re||ie==null&&re==null||Vt(t,n,_e,ie,o,re)}function n0(t){switch(t){case"css":case"script":case"font":case"img":case"image":case"input":case"link":return!0;default:return!1}}function hS(){if(typeof performance.getEntriesByType=="function"){for(var t=0,n=0,a=performance.getEntriesByType("resource"),o=0;o<a.length;o++){var c=a[o],f=c.transferSize,_=c.initiatorType,R=c.duration;if(f&&R&&n0(_)){for(_=0,R=c.responseEnd,o+=1;o<a.length;o++){var P=a[o],J=P.startTime;if(J>R)break;var fe=P.transferSize,_e=P.initiatorType;fe&&n0(_e)&&(P=P.responseEnd,_+=fe*(P<R?1:(R-J)/(P-J)))}if(--o,n+=8*(f+_)/(c.duration/1e3),t++,10<t)break}}if(0<t)return n/t/1e6}return navigator.connection&&(t=navigator.connection.downlink,typeof t=="number")?t:5}var Wf=null,qf=null;function tc(t){return t.nodeType===9?t:t.ownerDocument}function i0(t){switch(t){case"http://www.w3.org/2000/svg":return 1;case"http://www.w3.org/1998/Math/MathML":return 2;default:return 0}}function a0(t,n){if(t===0)switch(n){case"svg":return 1;case"math":return 2;default:return 0}return t===1&&n==="foreignObject"?0:t}function Yf(t,n){return t==="textarea"||t==="noscript"||typeof n.children=="string"||typeof n.children=="number"||typeof n.children=="bigint"||typeof n.dangerouslySetInnerHTML=="object"&&n.dangerouslySetInnerHTML!==null&&n.dangerouslySetInnerHTML.__html!=null}var Zf=null;function pS(){var t=window.event;return t&&t.type==="popstate"?t===Zf?!1:(Zf=t,!0):(Zf=null,!1)}var s0=typeof setTimeout=="function"?setTimeout:void 0,mS=typeof clearTimeout=="function"?clearTimeout:void 0,r0=typeof Promise=="function"?Promise:void 0,gS=typeof queueMicrotask=="function"?queueMicrotask:typeof r0<"u"?function(t){return r0.resolve(null).then(t).catch(_S)}:s0;function _S(t){setTimeout(function(){throw t})}function Ya(t){return t==="head"}function o0(t,n){var a=n,o=0;do{var c=a.nextSibling;if(t.removeChild(a),c&&c.nodeType===8)if(a=c.data,a==="/$"||a==="/&"){if(o===0){t.removeChild(c),Sr(n);return}o--}else if(a==="$"||a==="$?"||a==="$~"||a==="$!"||a==="&")o++;else if(a==="html")Uo(t.ownerDocument.documentElement);else if(a==="head"){a=t.ownerDocument.head,Uo(a);for(var f=a.firstChild;f;){var _=f.nextSibling,R=f.nodeName;f[Aa]||R==="SCRIPT"||R==="STYLE"||R==="LINK"&&f.rel.toLowerCase()==="stylesheet"||a.removeChild(f),f=_}}else a==="body"&&Uo(t.ownerDocument.body);a=c}while(a);Sr(n)}function l0(t,n){var a=t;t=0;do{var o=a.nextSibling;if(a.nodeType===1?n?(a._stashedDisplay=a.style.display,a.style.display="none"):(a.style.display=a._stashedDisplay||"",a.getAttribute("style")===""&&a.removeAttribute("style")):a.nodeType===3&&(n?(a._stashedText=a.nodeValue,a.nodeValue=""):a.nodeValue=a._stashedText||""),o&&o.nodeType===8)if(a=o.data,a==="/$"){if(t===0)break;t--}else a!=="$"&&a!=="$?"&&a!=="$~"&&a!=="$!"||t++;a=o}while(a)}function Kf(t){var n=t.firstChild;for(n&&n.nodeType===10&&(n=n.nextSibling);n;){var a=n;switch(n=n.nextSibling,a.nodeName){case"HTML":case"HEAD":case"BODY":Kf(a),Ra(a);continue;case"SCRIPT":case"STYLE":continue;case"LINK":if(a.rel.toLowerCase()==="stylesheet")continue}t.removeChild(a)}}function vS(t,n,a,o){for(;t.nodeType===1;){var c=a;if(t.nodeName.toLowerCase()!==n.toLowerCase()){if(!o&&(t.nodeName!=="INPUT"||t.type!=="hidden"))break}else if(o){if(!t[Aa])switch(n){case"meta":if(!t.hasAttribute("itemprop"))break;return t;case"link":if(f=t.getAttribute("rel"),f==="stylesheet"&&t.hasAttribute("data-precedence"))break;if(f!==c.rel||t.getAttribute("href")!==(c.href==null||c.href===""?null:c.href)||t.getAttribute("crossorigin")!==(c.crossOrigin==null?null:c.crossOrigin)||t.getAttribute("title")!==(c.title==null?null:c.title))break;return t;case"style":if(t.hasAttribute("data-precedence"))break;return t;case"script":if(f=t.getAttribute("src"),(f!==(c.src==null?null:c.src)||t.getAttribute("type")!==(c.type==null?null:c.type)||t.getAttribute("crossorigin")!==(c.crossOrigin==null?null:c.crossOrigin))&&f&&t.hasAttribute("async")&&!t.hasAttribute("itemprop"))break;return t;default:return t}}else if(n==="input"&&t.type==="hidden"){var f=c.name==null?null:""+c.name;if(c.type==="hidden"&&t.getAttribute("name")===f)return t}else return t;if(t=pi(t.nextSibling),t===null)break}return null}function xS(t,n,a){if(n==="")return null;for(;t.nodeType!==3;)if((t.nodeType!==1||t.nodeName!=="INPUT"||t.type!=="hidden")&&!a||(t=pi(t.nextSibling),t===null))return null;return t}function c0(t,n){for(;t.nodeType!==8;)if((t.nodeType!==1||t.nodeName!=="INPUT"||t.type!=="hidden")&&!n||(t=pi(t.nextSibling),t===null))return null;return t}function Qf(t){return t.data==="$?"||t.data==="$~"}function Jf(t){return t.data==="$!"||t.data==="$?"&&t.ownerDocument.readyState!=="loading"}function SS(t,n){var a=t.ownerDocument;if(t.data==="$~")t._reactRetry=n;else if(t.data!=="$?"||a.readyState!=="loading")n();else{var o=function(){n(),a.removeEventListener("DOMContentLoaded",o)};a.addEventListener("DOMContentLoaded",o),t._reactRetry=o}}function pi(t){for(;t!=null;t=t.nextSibling){var n=t.nodeType;if(n===1||n===3)break;if(n===8){if(n=t.data,n==="$"||n==="$!"||n==="$?"||n==="$~"||n==="&"||n==="F!"||n==="F")break;if(n==="/$"||n==="/&")return null}}return t}var $f=null;function u0(t){t=t.nextSibling;for(var n=0;t;){if(t.nodeType===8){var a=t.data;if(a==="/$"||a==="/&"){if(n===0)return pi(t.nextSibling);n--}else a!=="$"&&a!=="$!"&&a!=="$?"&&a!=="$~"&&a!=="&"||n++}t=t.nextSibling}return null}function f0(t){t=t.previousSibling;for(var n=0;t;){if(t.nodeType===8){var a=t.data;if(a==="$"||a==="$!"||a==="$?"||a==="$~"||a==="&"){if(n===0)return t;n--}else a!=="/$"&&a!=="/&"||n++}t=t.previousSibling}return null}function d0(t,n,a){switch(n=tc(a),t){case"html":if(t=n.documentElement,!t)throw Error(r(452));return t;case"head":if(t=n.head,!t)throw Error(r(453));return t;case"body":if(t=n.body,!t)throw Error(r(454));return t;default:throw Error(r(451))}}function Uo(t){for(var n=t.attributes;n.length;)t.removeAttributeNode(n[0]);Ra(t)}var mi=new Map,h0=new Set;function nc(t){return typeof t.getRootNode=="function"?t.getRootNode():t.nodeType===9?t:t.ownerDocument}var ha=I.d;I.d={f:MS,r:yS,D:ES,C:bS,L:TS,m:AS,X:CS,S:RS,M:wS};function MS(){var t=ha.f(),n=Yl();return t||n}function yS(t){var n=Qi(t);n!==null&&n.tag===5&&n.type==="form"?Dm(n):ha.r(t)}var _r=typeof document>"u"?null:document;function p0(t,n,a){var o=_r;if(o&&typeof n=="string"&&n){var c=Pt(n);c='link[rel="'+t+'"][href="'+c+'"]',typeof a=="string"&&(c+='[crossorigin="'+a+'"]'),h0.has(c)||(h0.add(c),t={rel:t,crossOrigin:a,href:n},o.querySelector(c)===null&&(n=o.createElement("link"),wn(n,"link",t),hn(n),o.head.appendChild(n)))}}function ES(t){ha.D(t),p0("dns-prefetch",t,null)}function bS(t,n){ha.C(t,n),p0("preconnect",t,n)}function TS(t,n,a){ha.L(t,n,a);var o=_r;if(o&&t&&n){var c='link[rel="preload"][as="'+Pt(n)+'"]';n==="image"&&a&&a.imageSrcSet?(c+='[imagesrcset="'+Pt(a.imageSrcSet)+'"]',typeof a.imageSizes=="string"&&(c+='[imagesizes="'+Pt(a.imageSizes)+'"]')):c+='[href="'+Pt(t)+'"]';var f=c;switch(n){case"style":f=vr(t);break;case"script":f=xr(t)}mi.has(f)||(t=x({rel:"preload",href:n==="image"&&a&&a.imageSrcSet?void 0:t,as:n},a),mi.set(f,t),o.querySelector(c)!==null||n==="style"&&o.querySelector(Lo(f))||n==="script"&&o.querySelector(No(f))||(n=o.createElement("link"),wn(n,"link",t),hn(n),o.head.appendChild(n)))}}function AS(t,n){ha.m(t,n);var a=_r;if(a&&t){var o=n&&typeof n.as=="string"?n.as:"script",c='link[rel="modulepreload"][as="'+Pt(o)+'"][href="'+Pt(t)+'"]',f=c;switch(o){case"audioworklet":case"paintworklet":case"serviceworker":case"sharedworker":case"worker":case"script":f=xr(t)}if(!mi.has(f)&&(t=x({rel:"modulepreload",href:t},n),mi.set(f,t),a.querySelector(c)===null)){switch(o){case"audioworklet":case"paintworklet":case"serviceworker":case"sharedworker":case"worker":case"script":if(a.querySelector(No(f)))return}o=a.createElement("link"),wn(o,"link",t),hn(o),a.head.appendChild(o)}}}function RS(t,n,a){ha.S(t,n,a);var o=_r;if(o&&t){var c=Ca(o).hoistableStyles,f=vr(t);n=n||"default";var _=c.get(f);if(!_){var R={loading:0,preload:null};if(_=o.querySelector(Lo(f)))R.loading=5;else{t=x({rel:"stylesheet",href:t,"data-precedence":n},a),(a=mi.get(f))&&jf(t,a);var P=_=o.createElement("link");hn(P),wn(P,"link",t),P._p=new Promise(function(J,fe){P.onload=J,P.onerror=fe}),P.addEventListener("load",function(){R.loading|=1}),P.addEventListener("error",function(){R.loading|=2}),R.loading|=4,ic(_,n,o)}_={type:"stylesheet",instance:_,count:1,state:R},c.set(f,_)}}}function CS(t,n){ha.X(t,n);var a=_r;if(a&&t){var o=Ca(a).hoistableScripts,c=xr(t),f=o.get(c);f||(f=a.querySelector(No(c)),f||(t=x({src:t,async:!0},n),(n=mi.get(c))&&ed(t,n),f=a.createElement("script"),hn(f),wn(f,"link",t),a.head.appendChild(f)),f={type:"script",instance:f,count:1,state:null},o.set(c,f))}}function wS(t,n){ha.M(t,n);var a=_r;if(a&&t){var o=Ca(a).hoistableScripts,c=xr(t),f=o.get(c);f||(f=a.querySelector(No(c)),f||(t=x({src:t,async:!0,type:"module"},n),(n=mi.get(c))&&ed(t,n),f=a.createElement("script"),hn(f),wn(f,"link",t),a.head.appendChild(f)),f={type:"script",instance:f,count:1,state:null},o.set(c,f))}}function m0(t,n,a,o){var c=(c=ue.current)?nc(c):null;if(!c)throw Error(r(446));switch(t){case"meta":case"title":return null;case"style":return typeof a.precedence=="string"&&typeof a.href=="string"?(n=vr(a.href),a=Ca(c).hoistableStyles,o=a.get(n),o||(o={type:"style",instance:null,count:0,state:null},a.set(n,o)),o):{type:"void",instance:null,count:0,state:null};case"link":if(a.rel==="stylesheet"&&typeof a.href=="string"&&typeof a.precedence=="string"){t=vr(a.href);var f=Ca(c).hoistableStyles,_=f.get(t);if(_||(c=c.ownerDocument||c,_={type:"stylesheet",instance:null,count:0,state:{loading:0,preload:null}},f.set(t,_),(f=c.querySelector(Lo(t)))&&!f._p&&(_.instance=f,_.state.loading=5),mi.has(t)||(a={rel:"preload",as:"style",href:a.href,crossOrigin:a.crossOrigin,integrity:a.integrity,media:a.media,hrefLang:a.hrefLang,referrerPolicy:a.referrerPolicy},mi.set(t,a),f||DS(c,t,a,_.state))),n&&o===null)throw Error(r(528,""));return _}if(n&&o!==null)throw Error(r(529,""));return null;case"script":return n=a.async,a=a.src,typeof a=="string"&&n&&typeof n!="function"&&typeof n!="symbol"?(n=xr(a),a=Ca(c).hoistableScripts,o=a.get(n),o||(o={type:"script",instance:null,count:0,state:null},a.set(n,o)),o):{type:"void",instance:null,count:0,state:null};default:throw Error(r(444,t))}}function vr(t){return'href="'+Pt(t)+'"'}function Lo(t){return'link[rel="stylesheet"]['+t+"]"}function g0(t){return x({},t,{"data-precedence":t.precedence,precedence:null})}function DS(t,n,a,o){t.querySelector('link[rel="preload"][as="style"]['+n+"]")?o.loading=1:(n=t.createElement("link"),o.preload=n,n.addEventListener("load",function(){return o.loading|=1}),n.addEventListener("error",function(){return o.loading|=2}),wn(n,"link",a),hn(n),t.head.appendChild(n))}function xr(t){return'[src="'+Pt(t)+'"]'}function No(t){return"script[async]"+t}function _0(t,n,a){if(n.count++,n.instance===null)switch(n.type){case"style":var o=t.querySelector('style[data-href~="'+Pt(a.href)+'"]');if(o)return n.instance=o,hn(o),o;var c=x({},a,{"data-href":a.href,"data-precedence":a.precedence,href:null,precedence:null});return o=(t.ownerDocument||t).createElement("style"),hn(o),wn(o,"style",c),ic(o,a.precedence,t),n.instance=o;case"stylesheet":c=vr(a.href);var f=t.querySelector(Lo(c));if(f)return n.state.loading|=4,n.instance=f,hn(f),f;o=g0(a),(c=mi.get(c))&&jf(o,c),f=(t.ownerDocument||t).createElement("link"),hn(f);var _=f;return _._p=new Promise(function(R,P){_.onload=R,_.onerror=P}),wn(f,"link",o),n.state.loading|=4,ic(f,a.precedence,t),n.instance=f;case"script":return f=xr(a.src),(c=t.querySelector(No(f)))?(n.instance=c,hn(c),c):(o=a,(c=mi.get(f))&&(o=x({},a),ed(o,c)),t=t.ownerDocument||t,c=t.createElement("script"),hn(c),wn(c,"link",o),t.head.appendChild(c),n.instance=c);case"void":return null;default:throw Error(r(443,n.type))}else n.type==="stylesheet"&&(n.state.loading&4)===0&&(o=n.instance,n.state.loading|=4,ic(o,a.precedence,t));return n.instance}function ic(t,n,a){for(var o=a.querySelectorAll('link[rel="stylesheet"][data-precedence],style[data-precedence]'),c=o.length?o[o.length-1]:null,f=c,_=0;_<o.length;_++){var R=o[_];if(R.dataset.precedence===n)f=R;else if(f!==c)break}f?f.parentNode.insertBefore(t,f.nextSibling):(n=a.nodeType===9?a.head:a,n.insertBefore(t,n.firstChild))}function jf(t,n){t.crossOrigin==null&&(t.crossOrigin=n.crossOrigin),t.referrerPolicy==null&&(t.referrerPolicy=n.referrerPolicy),t.title==null&&(t.title=n.title)}function ed(t,n){t.crossOrigin==null&&(t.crossOrigin=n.crossOrigin),t.referrerPolicy==null&&(t.referrerPolicy=n.referrerPolicy),t.integrity==null&&(t.integrity=n.integrity)}var ac=null;function v0(t,n,a){if(ac===null){var o=new Map,c=ac=new Map;c.set(a,o)}else c=ac,o=c.get(a),o||(o=new Map,c.set(a,o));if(o.has(t))return o;for(o.set(t,null),a=a.getElementsByTagName(t),c=0;c<a.length;c++){var f=a[c];if(!(f[Aa]||f[dn]||t==="link"&&f.getAttribute("rel")==="stylesheet")&&f.namespaceURI!=="http://www.w3.org/2000/svg"){var _=f.getAttribute(n)||"";_=t+_;var R=o.get(_);R?R.push(f):o.set(_,[f])}}return o}function x0(t,n,a){t=t.ownerDocument||t,t.head.insertBefore(a,n==="title"?t.querySelector("head > title"):null)}function US(t,n,a){if(a===1||n.itemProp!=null)return!1;switch(t){case"meta":case"title":return!0;case"style":if(typeof n.precedence!="string"||typeof n.href!="string"||n.href==="")break;return!0;case"link":if(typeof n.rel!="string"||typeof n.href!="string"||n.href===""||n.onLoad||n.onError)break;switch(n.rel){case"stylesheet":return t=n.disabled,typeof n.precedence=="string"&&t==null;default:return!0}case"script":if(n.async&&typeof n.async!="function"&&typeof n.async!="symbol"&&!n.onLoad&&!n.onError&&n.src&&typeof n.src=="string")return!0}return!1}function S0(t){return!(t.type==="stylesheet"&&(t.state.loading&3)===0)}function LS(t,n,a,o){if(a.type==="stylesheet"&&(typeof o.media!="string"||matchMedia(o.media).matches!==!1)&&(a.state.loading&4)===0){if(a.instance===null){var c=vr(o.href),f=n.querySelector(Lo(c));if(f){n=f._p,n!==null&&typeof n=="object"&&typeof n.then=="function"&&(t.count++,t=sc.bind(t),n.then(t,t)),a.state.loading|=4,a.instance=f,hn(f);return}f=n.ownerDocument||n,o=g0(o),(c=mi.get(c))&&jf(o,c),f=f.createElement("link"),hn(f);var _=f;_._p=new Promise(function(R,P){_.onload=R,_.onerror=P}),wn(f,"link",o),a.instance=f}t.stylesheets===null&&(t.stylesheets=new Map),t.stylesheets.set(a,n),(n=a.state.preload)&&(a.state.loading&3)===0&&(t.count++,a=sc.bind(t),n.addEventListener("load",a),n.addEventListener("error",a))}}var td=0;function NS(t,n){return t.stylesheets&&t.count===0&&oc(t,t.stylesheets),0<t.count||0<t.imgCount?function(a){var o=setTimeout(function(){if(t.stylesheets&&oc(t,t.stylesheets),t.unsuspend){var f=t.unsuspend;t.unsuspend=null,f()}},6e4+n);0<t.imgBytes&&td===0&&(td=62500*hS());var c=setTimeout(function(){if(t.waitingForImages=!1,t.count===0&&(t.stylesheets&&oc(t,t.stylesheets),t.unsuspend)){var f=t.unsuspend;t.unsuspend=null,f()}},(t.imgBytes>td?50:800)+n);return t.unsuspend=a,function(){t.unsuspend=null,clearTimeout(o),clearTimeout(c)}}:null}function sc(){if(this.count--,this.count===0&&(this.imgCount===0||!this.waitingForImages)){if(this.stylesheets)oc(this,this.stylesheets);else if(this.unsuspend){var t=this.unsuspend;this.unsuspend=null,t()}}}var rc=null;function oc(t,n){t.stylesheets=null,t.unsuspend!==null&&(t.count++,rc=new Map,n.forEach(OS,t),rc=null,sc.call(t))}function OS(t,n){if(!(n.state.loading&4)){var a=rc.get(t);if(a)var o=a.get(null);else{a=new Map,rc.set(t,a);for(var c=t.querySelectorAll("link[data-precedence],style[data-precedence]"),f=0;f<c.length;f++){var _=c[f];(_.nodeName==="LINK"||_.getAttribute("media")!=="not all")&&(a.set(_.dataset.precedence,_),o=_)}o&&a.set(null,o)}c=n.instance,_=c.getAttribute("data-precedence"),f=a.get(_)||o,f===o&&a.set(null,c),a.set(_,c),this.count++,o=sc.bind(this),c.addEventListener("load",o),c.addEventListener("error",o),f?f.parentNode.insertBefore(c,f.nextSibling):(t=t.nodeType===9?t.head:t,t.insertBefore(c,t.firstChild)),n.state.loading|=4}}var Oo={$$typeof:F,Provider:null,Consumer:null,_currentValue:$,_currentValue2:$,_threadCount:0};function PS(t,n,a,o,c,f,_,R,P){this.tag=1,this.containerInfo=t,this.pingCache=this.current=this.pendingChildren=null,this.timeoutHandle=-1,this.callbackNode=this.next=this.pendingContext=this.context=this.cancelPendingCommit=null,this.callbackPriority=0,this.expirationTimes=ke(-1),this.entangledLanes=this.shellSuspendCounter=this.errorRecoveryDisabledLanes=this.expiredLanes=this.warmLanes=this.pingedLanes=this.suspendedLanes=this.pendingLanes=0,this.entanglements=ke(0),this.hiddenUpdates=ke(null),this.identifierPrefix=o,this.onUncaughtError=c,this.onCaughtError=f,this.onRecoverableError=_,this.pooledCache=null,this.pooledCacheLanes=0,this.formState=P,this.incompleteTransitions=new Map}function M0(t,n,a,o,c,f,_,R,P,J,fe,_e){return t=new PS(t,n,a,_,P,J,fe,_e,R),n=1,f===!0&&(n|=24),f=ei(3,null,null,n),t.current=f,f.stateNode=t,n=Nu(),n.refCount++,t.pooledCache=n,n.refCount++,f.memoizedState={element:o,isDehydrated:a,cache:n},Fu(f),t}function y0(t){return t?(t=Qs,t):Qs}function E0(t,n,a,o,c,f){c=y0(c),o.context===null?o.context=c:o.pendingContext=c,o=za(n),o.payload={element:a},f=f===void 0?null:f,f!==null&&(o.callback=f),a=Fa(t,o,n),a!==null&&(qn(a,t,n),fo(a,t,n))}function b0(t,n){if(t=t.memoizedState,t!==null&&t.dehydrated!==null){var a=t.retryLane;t.retryLane=a!==0&&a<n?a:n}}function nd(t,n){b0(t,n),(t=t.alternate)&&b0(t,n)}function T0(t){if(t.tag===13||t.tag===31){var n=gs(t,67108864);n!==null&&qn(n,t,67108864),nd(t,67108864)}}function A0(t){if(t.tag===13||t.tag===31){var n=si();n=Yr(n);var a=gs(t,n);a!==null&&qn(a,t,n),nd(t,n)}}var lc=!0;function zS(t,n,a,o){var c=N.T;N.T=null;var f=I.p;try{I.p=2,id(t,n,a,o)}finally{I.p=f,N.T=c}}function FS(t,n,a,o){var c=N.T;N.T=null;var f=I.p;try{I.p=8,id(t,n,a,o)}finally{I.p=f,N.T=c}}function id(t,n,a,o){if(lc){var c=ad(o);if(c===null)kf(t,n,o,cc,a),C0(t,o);else if(IS(c,t,n,a,o))o.stopPropagation();else if(C0(t,o),n&4&&-1<BS.indexOf(t)){for(;c!==null;){var f=Qi(c);if(f!==null)switch(f.tag){case 3:if(f=f.stateNode,f.current.memoizedState.isDehydrated){var _=Ee(f.pendingLanes);if(_!==0){var R=f;for(R.pendingLanes|=2,R.entangledLanes|=2;_;){var P=1<<31-Pe(_);R.entanglements[1]|=P,_&=~P}Bi(f),(Dt&6)===0&&(Wl=Ht()+500,Co(0))}}break;case 31:case 13:R=gs(f,2),R!==null&&qn(R,f,2),Yl(),nd(f,2)}if(f=ad(o),f===null&&kf(t,n,o,cc,a),f===c)break;c=f}c!==null&&o.stopPropagation()}else kf(t,n,o,null,a)}}function ad(t){return t=su(t),sd(t)}var cc=null;function sd(t){if(cc=null,t=Ki(t),t!==null){var n=u(t);if(n===null)t=null;else{var a=n.tag;if(a===13){if(t=d(n),t!==null)return t;t=null}else if(a===31){if(t=h(n),t!==null)return t;t=null}else if(a===3){if(n.stateNode.current.memoizedState.isDehydrated)return n.tag===3?n.stateNode.containerInfo:null;t=null}else n!==t&&(t=null)}}return cc=t,null}function R0(t){switch(t){case"beforetoggle":case"cancel":case"click":case"close":case"contextmenu":case"copy":case"cut":case"auxclick":case"dblclick":case"dragend":case"dragstart":case"drop":case"focusin":case"focusout":case"input":case"invalid":case"keydown":case"keypress":case"keyup":case"mousedown":case"mouseup":case"paste":case"pause":case"play":case"pointercancel":case"pointerdown":case"pointerup":case"ratechange":case"reset":case"resize":case"seeked":case"submit":case"toggle":case"touchcancel":case"touchend":case"touchstart":case"volumechange":case"change":case"selectionchange":case"textInput":case"compositionstart":case"compositionend":case"compositionupdate":case"beforeblur":case"afterblur":case"beforeinput":case"blur":case"fullscreenchange":case"focus":case"hashchange":case"popstate":case"select":case"selectstart":return 2;case"drag":case"dragenter":case"dragexit":case"dragleave":case"dragover":case"mousemove":case"mouseout":case"mouseover":case"pointermove":case"pointerout":case"pointerover":case"scroll":case"touchmove":case"wheel":case"mouseenter":case"mouseleave":case"pointerenter":case"pointerleave":return 8;case"message":switch(wt()){case w:return 2;case M:return 8;case Y:case te:return 32;case le:return 268435456;default:return 32}default:return 32}}var rd=!1,Za=null,Ka=null,Qa=null,Po=new Map,zo=new Map,Ja=[],BS="mousedown mouseup touchcancel touchend touchstart auxclick dblclick pointercancel pointerdown pointerup dragend dragstart drop compositionend compositionstart keydown keypress keyup input textInput copy cut paste click change contextmenu reset".split(" ");function C0(t,n){switch(t){case"focusin":case"focusout":Za=null;break;case"dragenter":case"dragleave":Ka=null;break;case"mouseover":case"mouseout":Qa=null;break;case"pointerover":case"pointerout":Po.delete(n.pointerId);break;case"gotpointercapture":case"lostpointercapture":zo.delete(n.pointerId)}}function Fo(t,n,a,o,c,f){return t===null||t.nativeEvent!==f?(t={blockedOn:n,domEventName:a,eventSystemFlags:o,nativeEvent:f,targetContainers:[c]},n!==null&&(n=Qi(n),n!==null&&T0(n)),t):(t.eventSystemFlags|=o,n=t.targetContainers,c!==null&&n.indexOf(c)===-1&&n.push(c),t)}function IS(t,n,a,o,c){switch(n){case"focusin":return Za=Fo(Za,t,n,a,o,c),!0;case"dragenter":return Ka=Fo(Ka,t,n,a,o,c),!0;case"mouseover":return Qa=Fo(Qa,t,n,a,o,c),!0;case"pointerover":var f=c.pointerId;return Po.set(f,Fo(Po.get(f)||null,t,n,a,o,c)),!0;case"gotpointercapture":return f=c.pointerId,zo.set(f,Fo(zo.get(f)||null,t,n,a,o,c)),!0}return!1}function w0(t){var n=Ki(t.target);if(n!==null){var a=u(n);if(a!==null){if(n=a.tag,n===13){if(n=d(a),n!==null){t.blockedOn=n,ks(t.priority,function(){A0(a)});return}}else if(n===31){if(n=h(a),n!==null){t.blockedOn=n,ks(t.priority,function(){A0(a)});return}}else if(n===3&&a.stateNode.current.memoizedState.isDehydrated){t.blockedOn=a.tag===3?a.stateNode.containerInfo:null;return}}}t.blockedOn=null}function uc(t){if(t.blockedOn!==null)return!1;for(var n=t.targetContainers;0<n.length;){var a=ad(t.nativeEvent);if(a===null){a=t.nativeEvent;var o=new a.constructor(a.type,a);au=o,a.target.dispatchEvent(o),au=null}else return n=Qi(a),n!==null&&T0(n),t.blockedOn=a,!1;n.shift()}return!0}function D0(t,n,a){uc(t)&&a.delete(n)}function HS(){rd=!1,Za!==null&&uc(Za)&&(Za=null),Ka!==null&&uc(Ka)&&(Ka=null),Qa!==null&&uc(Qa)&&(Qa=null),Po.forEach(D0),zo.forEach(D0)}function fc(t,n){t.blockedOn===n&&(t.blockedOn=null,rd||(rd=!0,s.unstable_scheduleCallback(s.unstable_NormalPriority,HS)))}var dc=null;function U0(t){dc!==t&&(dc=t,s.unstable_scheduleCallback(s.unstable_NormalPriority,function(){dc===t&&(dc=null);for(var n=0;n<t.length;n+=3){var a=t[n],o=t[n+1],c=t[n+2];if(typeof o!="function"){if(sd(o||a)===null)continue;break}var f=Qi(a);f!==null&&(t.splice(n,3),n-=3,af(f,{pending:!0,data:c,method:a.method,action:o},o,c))}}))}function Sr(t){function n(P){return fc(P,t)}Za!==null&&fc(Za,t),Ka!==null&&fc(Ka,t),Qa!==null&&fc(Qa,t),Po.forEach(n),zo.forEach(n);for(var a=0;a<Ja.length;a++){var o=Ja[a];o.blockedOn===t&&(o.blockedOn=null)}for(;0<Ja.length&&(a=Ja[0],a.blockedOn===null);)w0(a),a.blockedOn===null&&Ja.shift();if(a=(t.ownerDocument||t).$$reactFormReplay,a!=null)for(o=0;o<a.length;o+=3){var c=a[o],f=a[o+1],_=c[Tn]||null;if(typeof f=="function")_||U0(a);else if(_){var R=null;if(f&&f.hasAttribute("formAction")){if(c=f,_=f[Tn]||null)R=_.formAction;else if(sd(c)!==null)continue}else R=_.action;typeof R=="function"?a[o+1]=R:(a.splice(o,3),o-=3),U0(a)}}}function L0(){function t(f){f.canIntercept&&f.info==="react-transition"&&f.intercept({handler:function(){return new Promise(function(_){return c=_})},focusReset:"manual",scroll:"manual"})}function n(){c!==null&&(c(),c=null),o||setTimeout(a,20)}function a(){if(!o&&!navigation.transition){var f=navigation.currentEntry;f&&f.url!=null&&navigation.navigate(f.url,{state:f.getState(),info:"react-transition",history:"replace"})}}if(typeof navigation=="object"){var o=!1,c=null;return navigation.addEventListener("navigate",t),navigation.addEventListener("navigatesuccess",n),navigation.addEventListener("navigateerror",n),setTimeout(a,100),function(){o=!0,navigation.removeEventListener("navigate",t),navigation.removeEventListener("navigatesuccess",n),navigation.removeEventListener("navigateerror",n),c!==null&&(c(),c=null)}}}function od(t){this._internalRoot=t}hc.prototype.render=od.prototype.render=function(t){var n=this._internalRoot;if(n===null)throw Error(r(409));var a=n.current,o=si();E0(a,o,t,n,null,null)},hc.prototype.unmount=od.prototype.unmount=function(){var t=this._internalRoot;if(t!==null){this._internalRoot=null;var n=t.containerInfo;E0(t.current,2,null,t,null,null),Yl(),n[Hn]=null}};function hc(t){this._internalRoot=t}hc.prototype.unstable_scheduleHydration=function(t){if(t){var n=Kr();t={blockedOn:null,target:t,priority:n};for(var a=0;a<Ja.length&&n!==0&&n<Ja[a].priority;a++);Ja.splice(a,0,t),a===0&&w0(t)}};var N0=e.version;if(N0!=="19.2.7")throw Error(r(527,N0,"19.2.7"));I.findDOMNode=function(t){var n=t._reactInternals;if(n===void 0)throw typeof t.render=="function"?Error(r(188)):(t=Object.keys(t).join(","),Error(r(268,t)));return t=p(n),t=t!==null?v(t):null,t=t===null?null:t.stateNode,t};var GS={bundleType:0,version:"19.2.7",rendererPackageName:"react-dom",currentDispatcherRef:N,reconcilerVersion:"19.2.7"};if(typeof __REACT_DEVTOOLS_GLOBAL_HOOK__<"u"){var pc=__REACT_DEVTOOLS_GLOBAL_HOOK__;if(!pc.isDisabled&&pc.supportsFiber)try{oe=pc.inject(GS),ce=pc}catch{}}return Io.createRoot=function(t,n){if(!l(t))throw Error(r(299));var a=!1,o="",c=Hm,f=Gm,_=Vm;return n!=null&&(n.unstable_strictMode===!0&&(a=!0),n.identifierPrefix!==void 0&&(o=n.identifierPrefix),n.onUncaughtError!==void 0&&(c=n.onUncaughtError),n.onCaughtError!==void 0&&(f=n.onCaughtError),n.onRecoverableError!==void 0&&(_=n.onRecoverableError)),n=M0(t,1,!1,null,null,a,o,null,c,f,_,L0),t[Hn]=n.current,Vf(t),new od(n)},Io.hydrateRoot=function(t,n,a){if(!l(t))throw Error(r(299));var o=!1,c="",f=Hm,_=Gm,R=Vm,P=null;return a!=null&&(a.unstable_strictMode===!0&&(o=!0),a.identifierPrefix!==void 0&&(c=a.identifierPrefix),a.onUncaughtError!==void 0&&(f=a.onUncaughtError),a.onCaughtError!==void 0&&(_=a.onCaughtError),a.onRecoverableError!==void 0&&(R=a.onRecoverableError),a.formState!==void 0&&(P=a.formState)),n=M0(t,1,!0,n,a??null,o,c,P,f,_,R,L0),n.context=y0(null),a=n.current,o=si(),o=Yr(o),c=za(o),c.callback=null,Fa(a,c,o),a=o,n.current.lanes=a,Ie(n,a),Bi(n),t[Hn]=n.current,Vf(t),new hc(n)},Io.version="19.2.7",Io}var I0;function KS(){if(I0)return cd.exports;I0=1;function s(){if(!(typeof __REACT_DEVTOOLS_GLOBAL_HOOK__>"u"||typeof __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE!="function"))try{__REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE(s)}catch(e){console.error(e)}}return s(),cd.exports=ZS(),cd.exports}var QS=KS();const Q_="ashare-theme";function JS(){const s=localStorage.getItem(Q_);return s==="dark"||s==="light"?s:"light"}function J_(s){document.documentElement.dataset.theme=s,localStorage.setItem(Q_,s)}const $S="modulepreload",jS=function(s){return"/"+s},H0={},Zi=function(e,i,r){let l=Promise.resolve();if(i&&i.length>0){let d=function(p){return Promise.all(p.map(v=>Promise.resolve(v).then(x=>({status:"fulfilled",value:x}),x=>({status:"rejected",reason:x}))))};document.getElementsByTagName("link");const h=document.querySelector("meta[property=csp-nonce]"),m=(h==null?void 0:h.nonce)||(h==null?void 0:h.getAttribute("nonce"));l=d(i.map(p=>{if(p=jS(p),p in H0)return;H0[p]=!0;const v=p.endsWith(".css"),x=v?'[rel="stylesheet"]':"";if(document.querySelector(`link[href="${p}"]${x}`))return;const g=document.createElement("link");if(g.rel=v?"stylesheet":$S,v||(g.as="script"),g.crossOrigin="",g.href=p,m&&g.setAttribute("nonce",m),document.head.appendChild(g),v)return new Promise((E,T)=>{g.addEventListener("load",E),g.addEventListener("error",()=>T(new Error(`Unable to preload CSS for ${p}`)))})}))}function u(d){const h=new Event("vite:preloadError",{cancelable:!0});if(h.payload=d,window.dispatchEvent(h),!h.defaultPrevented)throw d}return l.then(d=>{for(const h of d||[])h.status==="rejected"&&u(h.reason);return e().catch(u)})};/**
 * @license lucide-react v0.508.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const eM=s=>s.replace(/([a-z0-9])([A-Z])/g,"$1-$2").toLowerCase(),tM=s=>s.replace(/^([A-Z])|[\s-_]+(\w)/g,(e,i,r)=>r?r.toUpperCase():i.toLowerCase()),G0=s=>{const e=tM(s);return e.charAt(0).toUpperCase()+e.slice(1)},$_=(...s)=>s.filter((e,i,r)=>!!e&&e.trim()!==""&&r.indexOf(e)===i).join(" ").trim(),nM=s=>{for(const e in s)if(e.startsWith("aria-")||e==="role"||e==="title")return!0};/**
 * @license lucide-react v0.508.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */var iM={xmlns:"http://www.w3.org/2000/svg",width:24,height:24,viewBox:"0 0 24 24",fill:"none",stroke:"currentColor",strokeWidth:2,strokeLinecap:"round",strokeLinejoin:"round"};/**
 * @license lucide-react v0.508.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const aM=mt.forwardRef(({color:s="currentColor",size:e=24,strokeWidth:i=2,absoluteStrokeWidth:r,className:l="",children:u,iconNode:d,...h},m)=>mt.createElement("svg",{ref:m,...iM,width:e,height:e,stroke:s,strokeWidth:r?Number(i)*24/Number(e):i,className:$_("lucide",l),...!u&&!nM(h)&&{"aria-hidden":"true"},...h},[...d.map(([p,v])=>mt.createElement(p,v)),...Array.isArray(u)?u:[u]]));/**
 * @license lucide-react v0.508.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const In=(s,e)=>{const i=mt.forwardRef(({className:r,...l},u)=>mt.createElement(aM,{ref:u,iconNode:e,className:$_(`lucide-${eM(G0(s))}`,`lucide-${s}`,r),...l}));return i.displayName=G0(s),i};/**
 * @license lucide-react v0.508.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const sM=[["path",{d:"M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2",key:"169zse"}]],rM=In("activity",sM);/**
 * @license lucide-react v0.508.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const oM=[["path",{d:"M10.268 21a2 2 0 0 0 3.464 0",key:"vwvbt9"}],["path",{d:"M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326",key:"11g9vi"}]],lM=In("bell",oM);/**
 * @license lucide-react v0.508.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const cM=[["path",{d:"M3 3v16a2 2 0 0 0 2 2h16",key:"c24i48"}],["path",{d:"M18 17V9",key:"2bz60n"}],["path",{d:"M13 17V5",key:"1frdt8"}],["path",{d:"M8 17v-3",key:"17ska0"}]],uM=In("chart-column",cM);/**
 * @license lucide-react v0.508.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const fM=[["path",{d:"M3 3v16a2 2 0 0 0 2 2h16",key:"c24i48"}],["path",{d:"m19 9-5 5-4-4-3 3",key:"2osh9i"}]],dM=In("chart-line",fM);/**
 * @license lucide-react v0.508.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const hM=[["path",{d:"m16.24 7.76-1.804 5.411a2 2 0 0 1-1.265 1.265L7.76 16.24l1.804-5.411a2 2 0 0 1 1.265-1.265z",key:"9ktpf1"}],["circle",{cx:"12",cy:"12",r:"10",key:"1mglay"}]],pM=In("compass",hM);/**
 * @license lucide-react v0.508.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const mM=[["path",{d:"M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8",key:"1357e3"}],["path",{d:"M3 3v5h5",key:"1xhq8a"}],["path",{d:"M12 7v5l4 2",key:"1fdv2h"}]],gM=In("history",mM);/**
 * @license lucide-react v0.508.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const _M=[["path",{d:"M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83z",key:"zw3jo"}],["path",{d:"M2 12a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 12",key:"1wduqc"}],["path",{d:"M2 17a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 17",key:"kqbvx6"}]],vM=In("layers",_M);/**
 * @license lucide-react v0.508.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const xM=[["rect",{width:"7",height:"7",x:"3",y:"3",rx:"1",key:"1g98yp"}],["rect",{width:"7",height:"7",x:"14",y:"3",rx:"1",key:"6d4xhi"}],["rect",{width:"7",height:"7",x:"14",y:"14",rx:"1",key:"nxv5o0"}],["rect",{width:"7",height:"7",x:"3",y:"14",rx:"1",key:"1bb6yr"}]],SM=In("layout-grid",xM);/**
 * @license lucide-react v0.508.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const MM=[["path",{d:"m3 17 2 2 4-4",key:"1jhpwq"}],["path",{d:"m3 7 2 2 4-4",key:"1obspn"}],["path",{d:"M13 6h8",key:"15sg57"}],["path",{d:"M13 12h8",key:"h98zly"}],["path",{d:"M13 18h8",key:"oe0vm4"}]],yM=In("list-checks",MM);/**
 * @license lucide-react v0.508.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const EM=[["path",{d:"M21 12a9 9 0 1 1-6.219-8.56",key:"13zald"}]],V0=In("loader-circle",EM);/**
 * @license lucide-react v0.508.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const bM=[["path",{d:"M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4",key:"u53s6r"}],["polyline",{points:"10 17 15 12 10 7",key:"1ail0h"}],["line",{x1:"15",x2:"3",y1:"12",y2:"12",key:"v6grx8"}]],TM=In("log-in",bM);/**
 * @license lucide-react v0.508.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const AM=[["path",{d:"M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z",key:"a7tn18"}]],RM=In("moon",AM);/**
 * @license lucide-react v0.508.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const CM=[["path",{d:"M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8",key:"v9h5vc"}],["path",{d:"M21 3v5h-5",key:"1q7to0"}],["path",{d:"M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16",key:"3uifl3"}],["path",{d:"M8 16H3v5",key:"1cv678"}]],wM=In("refresh-cw",CM);/**
 * @license lucide-react v0.508.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const DM=[["path",{d:"M20 7h-9",key:"3s1dr2"}],["path",{d:"M14 17H5",key:"gfn3mx"}],["circle",{cx:"17",cy:"17",r:"3",key:"18b49y"}],["circle",{cx:"7",cy:"7",r:"3",key:"dfmy0x"}]],UM=In("settings-2",DM);/**
 * @license lucide-react v0.508.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const LM=[["circle",{cx:"12",cy:"12",r:"4",key:"4exip2"}],["path",{d:"M12 2v2",key:"tus03m"}],["path",{d:"M12 20v2",key:"1lh1kg"}],["path",{d:"m4.93 4.93 1.41 1.41",key:"149t6j"}],["path",{d:"m17.66 17.66 1.41 1.41",key:"ptbguv"}],["path",{d:"M2 12h2",key:"1t8f8n"}],["path",{d:"M20 12h2",key:"1q8mjw"}],["path",{d:"m6.34 17.66-1.41 1.41",key:"1m8zz5"}],["path",{d:"m19.07 4.93-1.41 1.41",key:"1shlcs"}]],NM=In("sun",LM);function OM(s){return{overview:e=>s("/api/dashboard/overview",void 0,{timeoutMs:(e==null?void 0:e.timeoutMs)??3e4,signal:e==null?void 0:e.signal})}}const PM=500,zr=[];function zM(){return new Date().toISOString()}let kd=null;const Xd=[],FM=5e3,BM=new Set(["warn","error","perf"]);function IM(s){BM.has(s.level)&&(Xd.push(s),kd||(kd=setTimeout(HM,FM)))}async function HM(){if(kd=null,Xd.length===0)return;const s=Xd.splice(0);try{await fetch("/api/frontend-logs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(s.map(e=>({level:e.level,category:e.category,message:e.message,extra:{...e.extra,duration:e.duration}})))})}catch{}}function GM(s){zr.push(s),zr.length>PM&&zr.shift();const e=`[${s.iso}] [${s.level.toUpperCase()}] ${s.category}:`,i=s.duration!==void 0?` (${s.duration}ms)`:"",r=s.extra?` ${JSON.stringify(s.extra)}`:"";switch(s.level){case"error":console.error(`${e} ${s.message}${i}${r}`);break;case"warn":case"perf":console.warn(`${e} ${s.message}${i}${r}`);break;case"info":console.info(`${e} ${s.message}${i}${r}`);break;default:console.debug(`${e} ${s.message}${i}${r}`)}IM(s)}function ja(s,e,i,r,l){GM({timestamp:Date.now(),iso:zM(),level:s,category:e,message:i,duration:l,extra:r})}const Ho={debug:(s,e,i)=>ja("debug",s,e,i),info:(s,e,i)=>ja("info",s,e,i),warn:(s,e,i)=>ja("warn",s,e,i),error:(s,e,i)=>ja("error",s,e,i),perf:(s,e,i)=>ja("perf",s,e,i),api:(s,e,i,r)=>ja(r>=400?"warn":"info","api",`${s} ${e} → ${r}`,void 0,i),stateChange:(s,e)=>ja("debug","state",`${s} changed (${e})`),perfMark:s=>{const e=performance.now();return{end:i=>{const r=Math.round(performance.now()-e);return ja(r>1e3?"warn":"debug","perf",s,i,r),r}}},getBuffer:()=>[...zr],clearBuffer:()=>{zr.length=0},exportRecent:(s=100)=>zr.slice(-s).map(i=>`${i.iso} [${i.level.toUpperCase()}] ${i.category}: ${i.message}${i.duration!==void 0?` (${i.duration}ms)`:""}${i.extra?` ${JSON.stringify(i.extra)}`:""}`).join(`
`)},VM=3e3,ws=3e4,kM=[[/lost connection to mysql server|pymysql\\.err\\.operationalerror|sqlalchemy\\.exc\\.operationalerror/i,"数据服务连接超时，请稍后重试；如果持续出现，请到控制中心查看数据池和服务状态。"],[/request failed:\\s*500|internal server error/i,"后端服务暂时不可用，请重试或到控制中心查看状态。"],[/failed to fetch|networkerror/i,"网络连接失败，请确认后端服务正在运行后重试。"]];let Wd=!1;typeof window<"u"&&(window.addEventListener("pagehide",()=>{Wd=!0}),window.addEventListener("beforeunload",()=>{Wd=!0}));async function Be(s,e,i){const r=(e==null?void 0:e.method)??"GET",l=i==null?void 0:i.timeoutMs,u=i==null?void 0:i.signal,d=l||u?new AbortController:null,h=()=>d==null?void 0:d.abort();u&&(u.aborted?d==null||d.abort():u.addEventListener("abort",h,{once:!0}));const m=l?window.setTimeout(()=>d==null?void 0:d.abort(),l):null,p=performance.now();try{const v=await fetch(s,{headers:{"Content-Type":"application/json"},signal:d==null?void 0:d.signal,...e}),x=Math.round(performance.now()-p);if(Ho.api(r,s,x,v.status),x>VM&&Ho.warn("api",`慢请求: ${r} ${s}`,{duration:x,status:v.status}),!v.ok){const g=await v.text();throw v.status===401?new Error("UNAUTHORIZED"):new Error(X0(g||`Request failed: ${v.status}`,v.status))}return await v.json()}catch(v){const x=Math.round(performance.now()-p);throw v.name==="AbortError"||u!=null&&u.aborted||k0(v)?u!=null&&u.aborted||k0(v)?(Ho.info("api",`${r} ${s} 已取消`,{duration:x}),XM()):(Ho.warn("api",`${r} ${s} 超时 (${l}ms)`,{duration:x}),new Error("请求超时，请检查网络后刷新重试")):v.message==="UNAUTHORIZED"?v:(Ho.error("api",`${r} ${s} failed`,{duration:x,error:String(v)}),new Error(X0(v)))}finally{m!==null&&clearTimeout(m),u==null||u.removeEventListener("abort",h)}}function XM(){const s=new Error("请求已取消");return s.name="AbortError",s}function k0(s){if(!Wd)return!1;const e=s instanceof Error?s.message:String(s??"");return/failed to fetch|networkerror|load failed/i.test(e)}function X0(s,e){const i=s instanceof Error?s.message:String(s??"");for(const[r,l]of kM)if(r.test(i))return l;return e===500?"后端服务暂时不可用，请重试或到控制中心查看状态。":e===404?"请求的数据不存在或接口尚未启用。":e===403?"当前没有权限执行这个操作。":i||"操作失败，请稍后重试。"}function Ct(s,e){return{method:s,body:e===void 0?void 0:JSON.stringify(e)}}async function mc(s,e,i){return Be(s,i,{timeoutMs:e})}const W0={authStatus:()=>Be("/api/auth/status"),login:s=>Be("/api/auth/login",Ct("POST",{password:s})),dashboard:OM(Be),bootstrap:()=>Be("/api/home/bootstrap"),initial:()=>Be("/api/home/initial"),presets:()=>Be("/api/presets",void 0,{timeoutMs:ws}),createPreset:s=>Be("/api/presets",Ct("POST",s)),updatePreset:(s,e)=>Be(`/api/presets/${s}`,Ct("PATCH",e)),runs:(s,e)=>{const i=new URLSearchParams;s&&i.set("run_mode",s),e&&i.set("limit",String(e));const r=i.toString();return Be(`/api/runs${r?`?${r}`:""}`)},runDetail:s=>Be(`/api/runs/${s}`),runRecommendations:s=>Be(`/api/runs/${s}/recommendations`),runFunnelAudit:s=>Be(`/api/runs/${s}/funnel`),createRun:s=>mc("/api/runs",3e5,Ct("POST",s)),startMatrixRun:s=>Be("/api/runs/matrix",Ct("POST",s??{})),matrixRunStatus:()=>Be("/api/runs/matrix/status"),recommendationDetail:s=>Be(`/api/recommendations/${s}`),recommendationHistory:s=>{const e=new URLSearchParams;return e.set("run_mode",(s==null?void 0:s.run_mode)??"formal"),s!=null&&s.code&&e.set("code",s.code),s!=null&&s.label&&e.set("label",s.label),s!=null&&s.limit&&e.set("limit",String(s.limit)),s!=null&&s.offset&&e.set("offset",String(s.offset)),Be(`/api/recommendations/history?${e.toString()}`)},replayAnalysis:s=>Be(`/api/recommendations/runs/${s}/replay-analysis`),watchRecommendation:(s,e)=>Be(`/api/recommendations/${s}/watch`,Ct("POST",e)),ignoreRecommendation:(s,e)=>Be(`/api/recommendations/${s}/ignore`,Ct("POST",e??{})),reviewRecommendation:(s,e)=>Be(`/api/recommendations/${s}/review`,Ct("POST",e)),labelOutcome:(s,e)=>Be(`/api/outcomes/${s}/label`,Ct("POST",e)),autoLabelOutcomes:s=>{const e=new URLSearchParams;s!=null&&s.horizon_days&&e.set("horizon_days",String(s.horizon_days)),s!=null&&s.run_mode&&e.set("run_mode",s.run_mode);const i=e.toString();return Be(`/api/outcomes/label${i?`?${i}`:""}`,Ct("POST"))},dataSources:()=>Be("/api/data-sources"),localPool:s=>{const e=new URLSearchParams;(s==null?void 0:s.u_limit)!==void 0&&e.set("u_limit",String(s.u_limit)),(s==null?void 0:s.u_offset)!==void 0&&e.set("u_offset",String(s.u_offset)),(s==null?void 0:s.b_limit)!==void 0&&e.set("b_limit",String(s.b_limit)),(s==null?void 0:s.b_offset)!==void 0&&e.set("b_offset",String(s.b_offset)),(s==null?void 0:s.include_details)!==void 0&&e.set("include_details",String(s.include_details));const i=e.toString();return Be(`/api/data-sources/pool${i?`?${i}`:""}`,void 0,{timeoutMs:ws})},refreshLocalPool:()=>mc("/api/data-sources/pool/refresh",3e5,Ct("POST")),syncLocalPoolGaps:s=>mc("/api/data-sources/pool/sync-gaps",3e5,Ct("POST",s??{})),setDataSourceEnabled:(s,e,i)=>Be(`/api/data-sources/${s}/${e}/enabled`,Ct("POST",{enabled:i})),modelRoutes:()=>Be("/api/admin/model-routes",void 0,{timeoutMs:ws}),createModelRoute:s=>Be("/api/admin/model-routes",Ct("POST",s)),testModelRoute:s=>Be("/api/admin/model-routes/test",Ct("POST",s),{timeoutMs:35e3}),updateModelRoute:(s,e)=>Be(`/api/admin/model-routes/${s}`,Ct("PATCH",e)),deleteModelRoute:s=>Be(`/api/admin/model-routes/${s}`,Ct("DELETE")),reorderModelRoutes:s=>Be("/api/admin/model-routes/reorder",Ct("POST",{route_ids:s})),scheduledTasks:()=>Be("/api/admin/tasks",void 0,{timeoutMs:ws}),scheduledTaskRuns:(s=100,e)=>Be(`/api/admin/task-runs?limit=${s}${e?`&task_id=${encodeURIComponent(e)}`:""}`,void 0,{timeoutMs:ws}),createScheduledTask:s=>Be("/api/admin/tasks",Ct("POST",s)),updateScheduledTask:(s,e)=>Be(`/api/admin/tasks/${s}`,Ct("PATCH",e)),deleteScheduledTask:s=>Be(`/api/admin/tasks/${s}`,Ct("DELETE")),runScheduledTask:s=>Be(`/api/admin/tasks/${s}/run`,Ct("POST")),skills:()=>Be("/api/skills",void 0,{timeoutMs:ws}),strategies:(s="formal")=>Be(`/api/strategies?mode=${s}`,void 0,{timeoutMs:ws}),installSkill:s=>Be("/api/skills/install",Ct("POST",s)),skillDetail:s=>Be(`/api/skills/${s}`),setSkillMode:(s,e)=>Be(`/api/skills/${s}/mode`,Ct("POST",{mode:e})),skillPerformanceSummary:s=>Be(`/api/skills/${s}/performance-summary`),batchSkillPerformanceSummaries:()=>Be("/api/skills/performance-summaries"),resumeRun:s=>mc(`/api/runs/${s}/resume`,3e5,Ct("POST")),deleteRun:s=>Be(`/api/runs/${s}`,Ct("DELETE")),deleteRecommendation:s=>Be(`/api/recommendations/${s}`,Ct("DELETE")),deletePreset:s=>Be(`/api/presets/${s}`,Ct("DELETE")),metricsOverview:(s,e)=>Be(`/api/metrics/overview?mode=${s}`,void 0,{timeoutMs:(e==null?void 0:e.timeoutMs)??3e4,signal:e==null?void 0:e.signal}),filterEffectiveness:(s,e)=>Be(`/api/metrics/filter-effectiveness?mode=${s}`,void 0,{timeoutMs:(e==null?void 0:e.timeoutMs)??15e3,signal:e==null?void 0:e.signal}),dataHealth:s=>Be("/api/admin/data-health",void 0,s),previewDataHealthRemediation:s=>Be("/api/admin/data-health/remediate",Ct("POST",s??{}),{timeoutMs:3e5}),startDataHealthRemediation:s=>Be("/api/admin/data-health/remediate",Ct("POST",s??{})),dataHealthRemediationStatus:()=>Be("/api/admin/data-health/remediate/status"),temporalConsistency:(s,e)=>Be(`/api/admin/temporal-consistency${s?`?run_mode=${s}`:""}`,void 0,e),previewMinuteGate:s=>Be("/api/runs/backtest",Ct("POST",{...s,action:"preview_minute_gate"}),{timeoutMs:12e4}),remediateMinuteGaps:s=>Be("/api/runs/backtest",Ct("POST",{...s,action:"remediate_minute_gaps"}),{timeoutMs:12e4}),statsOverview:s=>Be(`/api/stats/overview?mode=${s}`),statsBySkill:s=>Be(`/api/stats/by-skill?mode=${s}`),statsByPreset:s=>Be(`/api/stats/by-preset?mode=${s}`),metricsByStrategy:s=>Be(`/api/metrics/by-strategy?mode=${s}`),metricsBySetup:s=>Be(`/api/metrics/by-setup?mode=${s}`),precisionAtK:s=>Be(`/api/metrics/precision-at-k?mode=${s}`),metricsByScoreLine:s=>Be(`/api/metrics/by-score-line?mode=${s}`),runRecords:s=>{const e=new URLSearchParams;s!=null&&s.run_mode&&e.set("run_mode",s.run_mode),s!=null&&s.limit&&e.set("limit",String(s.limit));const i=e.toString();return Be(`/api/records/runs${i?`?${i}`:""}`)},runRecordDetail:s=>Be(`/api/records/runs/${s}`),matrixRecords:s=>{const e=new URLSearchParams;return s!=null&&s.page&&e.set("page",String(s.page)),s!=null&&s.page_size&&e.set("page_size",String(s.page_size)),s!=null&&s.code&&e.set("code",s.code),Be(`/api/records/matrix?${e.toString()}`)},matrixRecordDetail:s=>Be(`/api/records/matrix/${s}`)};function WM({children:s}){const[e,i]=mt.useState(!1),[r,l]=mt.useState(!1),[u,d]=mt.useState(""),[h,m]=mt.useState(""),[p,v]=mt.useState(!1);mt.useEffect(()=>{W0.authStatus().then(g=>l(!g.enabled||g.authenticated)).catch(()=>m("无法确认访问状态")).finally(()=>i(!0))},[]);async function x(g){g.preventDefault(),v(!0),m("");try{await W0.login(u),l(!0)}catch{m("访问密码不正确")}finally{v(!1)}}return e?r?We.jsx(We.Fragment,{children:s}):We.jsx("div",{className:"auth-screen",children:We.jsxs("form",{className:"auth-panel",onSubmit:x,children:[We.jsxs("div",{children:[We.jsx("div",{className:"auth-title",children:"短线交易驾驶舱"}),We.jsx("div",{className:"auth-subtitle",children:"A-share Shortline Cockpit"})]}),We.jsx("input",{className:"auth-input",type:"password",value:u,onChange:g=>d(g.target.value),"aria-label":"访问密码"}),h?We.jsx("div",{className:"auth-error",children:h}):null,We.jsxs("button",{className:"primary auth-submit",type:"submit",disabled:p||!u,children:[p?We.jsx(V0,{className:"spin",size:16}):We.jsx(TM,{size:16}),"进入"]})]})}):We.jsx("div",{className:"auth-screen",children:We.jsxs("div",{className:"auth-panel",children:[We.jsx(V0,{className:"spin",size:22}),We.jsx("span",{children:"正在进入驾驶舱"})]})})}const qM=[{section:"dashboard",label:"驾驶舱",icon:pM},{section:"pool",label:"候选池",icon:uM},{section:"history",label:"复盘",icon:gM},{section:"records",label:"记录",icon:yM},{section:"matrix",label:"矩阵",icon:SM},{section:"metrics",label:"指标",icon:dM},{section:"strategies",label:"策略",icon:vM},{section:"config",label:"配置",icon:UM}],j_={home:"/",dashboard:"/dashboard",pool:"/pool",history:"/history",records:"/records",matrix:"/matrix",metrics:"/metrics",strategies:"/strategies",config:"/config"};function YM(s){const e=s.replace(/\/+$/,"")||"/";if(e==="/")return"home";for(const[i,r]of Object.entries(j_))if(i!=="home"&&e===r)return i;return e==="/run"?"dashboard":"home"}function ZM(s,e){const i=e.replace(/\/+$/,"")||"/";return i===j_[s]||s==="config"&&(i==="/backtest"||i==="/data-health")||s==="matrix"&&(i==="/stock-matrix"||i==="/preset-matrix")||s==="dashboard"&&i==="/run"}function kR(s){return new URLSearchParams(window.location.search).get(s)}function KM(s,e){if(!e)return s;const i=new URLSearchParams;for(const[l,u]of Object.entries(e))u!=null&&u!==""&&i.set(l,String(u));const r=i.toString();return r?`${s}?${r}`:s}let ev=typeof window<"u"?window.location.pathname:"/";const qd=new Set;function tv(){ev=window.location.pathname,qd.forEach(s=>s())}typeof window<"u"&&window.addEventListener("popstate",tv);function QM(){const[,s]=mt.useState(0);mt.useEffect(()=>{const h=()=>s(m=>m+1);return qd.add(h),()=>{qd.delete(h)}},[]);const e=mt.useCallback((h,m)=>{const p=m!=null&&m.params?KM(h,m.params):h;m!=null&&m.replace?window.history.replaceState({},"",p):window.history.pushState({},"",p),tv(),window.scrollTo({top:0,behavior:"instant"})},[]),i=mt.useCallback((h,m)=>{const p=h==="dashboard"?"/dashboard":`/${h}`;e(p,m)},[e]),r=ev,l=YM(r),u=typeof window<"u"?window.location.search:"",d=new URLSearchParams(u);return{pathname:r,section:l,params:d,navigate:e,navigateSection:i}}function JM(){const[s,e]=mt.useState(()=>JS());mt.useEffect(()=>{J_(s)},[s]);const i=mt.useCallback(()=>{e(r=>r==="dark"?"light":"dark")},[]);return{theme:s,setTheme:e,toggleTheme:i}}/**
 * @license
 * Copyright 2010-2026 Three.js Authors
 * SPDX-License-Identifier: MIT
 */const Bh="185",$M=0,q0=1,jM=2,Gc=1,ey=2,Yo=3,ls=0,Yn=1,xa=2,Ma=0,Fr=1,Yd=2,Y0=3,Z0=4,ty=5,Ps=100,ny=101,iy=102,ay=103,sy=104,ry=200,oy=201,ly=202,cy=203,Zd=204,Kd=205,uy=206,fy=207,dy=208,hy=209,py=210,my=211,gy=212,_y=213,vy=214,Qd=0,Jd=1,$d=2,Hr=3,jd=4,eh=5,th=6,nh=7,nv=0,xy=1,Sy=2,Xi=0,iv=1,av=2,sv=3,rv=4,ov=5,lv=6,cv=7,uv=300,Is=301,Gr=302,dd=303,hd=304,eu=306,ih=1e3,Sa=1001,ah=1002,Dn=1003,My=1004,gc=1005,On=1006,pd=1007,Fs=1008,xi=1009,fv=1010,dv=1011,Jo=1012,Ih=1013,qi=1014,Vi=1015,ba=1016,Hh=1017,Gh=1018,$o=1020,hv=35902,pv=35899,mv=1021,gv=1022,Di=1023,Ta=1026,Bs=1027,_v=1028,Vh=1029,Hs=1030,kh=1031,Xh=1033,Vc=33776,kc=33777,Xc=33778,Wc=33779,sh=35840,rh=35841,oh=35842,lh=35843,ch=36196,uh=37492,fh=37496,dh=37488,hh=37489,Yc=37490,ph=37491,mh=37808,gh=37809,_h=37810,vh=37811,xh=37812,Sh=37813,Mh=37814,yh=37815,Eh=37816,bh=37817,Th=37818,Ah=37819,Rh=37820,Ch=37821,wh=36492,Dh=36494,Uh=36495,Lh=36283,Nh=36284,Zc=36285,Oh=36286,yy=3200,K0=0,Ey=1,rs="",_i="srgb",Kc="srgb-linear",Qc="linear",It="srgb",Mr=7680,Q0=519,by=512,Ty=513,Ay=514,Wh=515,Ry=516,Cy=517,qh=518,wy=519,J0=35044,$0="300 es",ki=2e3,Jc=2001;function Dy(s){for(let e=s.length-1;e>=0;--e)if(s[e]>=65535)return!0;return!1}function $c(s){return document.createElementNS("http://www.w3.org/1999/xhtml",s)}function Uy(){const s=$c("canvas");return s.style.display="block",s}const j0={};function e_(...s){const e="THREE."+s.shift();console.log(e,...s)}function vv(s){const e=s[0];if(typeof e=="string"&&e.startsWith("TSL:")){const i=s[1];i&&i.isStackTrace?s[0]+=" "+i.getLocation():s[1]='Stack trace not available. Enable "THREE.Node.captureStackTrace" to capture stack traces.'}return s}function et(...s){s=vv(s);const e="THREE."+s.shift();{const i=s[0];i&&i.isStackTrace?console.warn(i.getError(e)):console.warn(e,...s)}}function bt(...s){s=vv(s);const e="THREE."+s.shift();{const i=s[0];i&&i.isStackTrace?console.error(i.getError(e)):console.error(e,...s)}}function Br(...s){const e=s.join(" ");e in j0||(j0[e]=!0,et(...s))}function Ly(s,e,i){return new Promise(function(r,l){function u(){switch(s.clientWaitSync(e,s.SYNC_FLUSH_COMMANDS_BIT,0)){case s.WAIT_FAILED:l();break;case s.TIMEOUT_EXPIRED:setTimeout(u,i);break;default:r()}}setTimeout(u,i)})}const Ny={[Qd]:Jd,[$d]:th,[jd]:nh,[Hr]:eh,[Jd]:Qd,[th]:$d,[nh]:jd,[eh]:Hr};class Vs{addEventListener(e,i){this._listeners===void 0&&(this._listeners={});const r=this._listeners;r[e]===void 0&&(r[e]=[]),r[e].indexOf(i)===-1&&r[e].push(i)}hasEventListener(e,i){const r=this._listeners;return r===void 0?!1:r[e]!==void 0&&r[e].indexOf(i)!==-1}removeEventListener(e,i){const r=this._listeners;if(r===void 0)return;const l=r[e];if(l!==void 0){const u=l.indexOf(i);u!==-1&&l.splice(u,1)}}dispatchEvent(e){const i=this._listeners;if(i===void 0)return;const r=i[e.type];if(r!==void 0){e.target=this;const l=r.slice(0);for(let u=0,d=l.length;u<d;u++)l[u].call(this,e);e.target=null}}}const Ln=["00","01","02","03","04","05","06","07","08","09","0a","0b","0c","0d","0e","0f","10","11","12","13","14","15","16","17","18","19","1a","1b","1c","1d","1e","1f","20","21","22","23","24","25","26","27","28","29","2a","2b","2c","2d","2e","2f","30","31","32","33","34","35","36","37","38","39","3a","3b","3c","3d","3e","3f","40","41","42","43","44","45","46","47","48","49","4a","4b","4c","4d","4e","4f","50","51","52","53","54","55","56","57","58","59","5a","5b","5c","5d","5e","5f","60","61","62","63","64","65","66","67","68","69","6a","6b","6c","6d","6e","6f","70","71","72","73","74","75","76","77","78","79","7a","7b","7c","7d","7e","7f","80","81","82","83","84","85","86","87","88","89","8a","8b","8c","8d","8e","8f","90","91","92","93","94","95","96","97","98","99","9a","9b","9c","9d","9e","9f","a0","a1","a2","a3","a4","a5","a6","a7","a8","a9","aa","ab","ac","ad","ae","af","b0","b1","b2","b3","b4","b5","b6","b7","b8","b9","ba","bb","bc","bd","be","bf","c0","c1","c2","c3","c4","c5","c6","c7","c8","c9","ca","cb","cc","cd","ce","cf","d0","d1","d2","d3","d4","d5","d6","d7","d8","d9","da","db","dc","dd","de","df","e0","e1","e2","e3","e4","e5","e6","e7","e8","e9","ea","eb","ec","ed","ee","ef","f0","f1","f2","f3","f4","f5","f6","f7","f8","f9","fa","fb","fc","fd","fe","ff"];let t_=1234567;const Ko=Math.PI/180,jo=180/Math.PI;function Xr(){const s=Math.random()*4294967295|0,e=Math.random()*4294967295|0,i=Math.random()*4294967295|0,r=Math.random()*4294967295|0;return(Ln[s&255]+Ln[s>>8&255]+Ln[s>>16&255]+Ln[s>>24&255]+"-"+Ln[e&255]+Ln[e>>8&255]+"-"+Ln[e>>16&15|64]+Ln[e>>24&255]+"-"+Ln[i&63|128]+Ln[i>>8&255]+"-"+Ln[i>>16&255]+Ln[i>>24&255]+Ln[r&255]+Ln[r>>8&255]+Ln[r>>16&255]+Ln[r>>24&255]).toLowerCase()}function vt(s,e,i){return Math.max(e,Math.min(i,s))}function Yh(s,e){return(s%e+e)%e}function Oy(s,e,i,r,l){return r+(s-e)*(l-r)/(i-e)}function Py(s,e,i){return s!==e?(i-s)/(e-s):0}function Qo(s,e,i){return(1-i)*s+i*e}function zy(s,e,i,r){return Qo(s,e,1-Math.exp(-i*r))}function Fy(s,e=1){return e-Math.abs(Yh(s,e*2)-e)}function By(s,e,i){return s<=e?0:s>=i?1:(s=(s-e)/(i-e),s*s*(3-2*s))}function Iy(s,e,i){return s<=e?0:s>=i?1:(s=(s-e)/(i-e),s*s*s*(s*(s*6-15)+10))}function Hy(s,e){return s+Math.floor(Math.random()*(e-s+1))}function Gy(s,e){return s+Math.random()*(e-s)}function Vy(s){return s*(.5-Math.random())}function ky(s){s!==void 0&&(t_=s);let e=t_+=1831565813;return e=Math.imul(e^e>>>15,e|1),e^=e+Math.imul(e^e>>>7,e|61),((e^e>>>14)>>>0)/4294967296}function Xy(s){return s*Ko}function Wy(s){return s*jo}function qy(s){return(s&s-1)===0&&s!==0}function Yy(s){return Math.pow(2,Math.ceil(Math.log(s)/Math.LN2))}function Zy(s){return Math.pow(2,Math.floor(Math.log(s)/Math.LN2))}function Ky(s,e,i,r,l){const u=Math.cos,d=Math.sin,h=u(i/2),m=d(i/2),p=u((e+r)/2),v=d((e+r)/2),x=u((e-r)/2),g=d((e-r)/2),E=u((r-e)/2),T=d((r-e)/2);switch(l){case"XYX":s.set(h*v,m*x,m*g,h*p);break;case"YZY":s.set(m*g,h*v,m*x,h*p);break;case"ZXZ":s.set(m*x,m*g,h*v,h*p);break;case"XZX":s.set(h*v,m*T,m*E,h*p);break;case"YXY":s.set(m*E,h*v,m*T,h*p);break;case"ZYZ":s.set(m*T,m*E,h*v,h*p);break;default:et("MathUtils: .setQuaternionFromProperEuler() encountered an unknown order: "+l)}}function Pr(s,e){switch(e.constructor){case Float32Array:return s;case Uint32Array:return s/4294967295;case Uint16Array:return s/65535;case Uint8Array:return s/255;case Int32Array:return Math.max(s/2147483647,-1);case Int16Array:return Math.max(s/32767,-1);case Int8Array:return Math.max(s/127,-1);default:throw new Error("THREE.MathUtils: Invalid component type.")}}function zn(s,e){switch(e.constructor){case Float32Array:return s;case Uint32Array:return Math.round(s*4294967295);case Uint16Array:return Math.round(s*65535);case Uint8Array:return Math.round(s*255);case Int32Array:return Math.round(s*2147483647);case Int16Array:return Math.round(s*32767);case Int8Array:return Math.round(s*127);default:throw new Error("THREE.MathUtils: Invalid component type.")}}const n_={DEG2RAD:Ko,RAD2DEG:jo,generateUUID:Xr,clamp:vt,euclideanModulo:Yh,mapLinear:Oy,inverseLerp:Py,lerp:Qo,damp:zy,pingpong:Fy,smoothstep:By,smootherstep:Iy,randInt:Hy,randFloat:Gy,randFloatSpread:Vy,seededRandom:ky,degToRad:Xy,radToDeg:Wy,isPowerOfTwo:qy,ceilPowerOfTwo:Yy,floorPowerOfTwo:Zy,setQuaternionFromProperEuler:Ky,normalize:zn,denormalize:Pr},Jh=class Jh{constructor(e=0,i=0){this.x=e,this.y=i}get width(){return this.x}set width(e){this.x=e}get height(){return this.y}set height(e){this.y=e}set(e,i){return this.x=e,this.y=i,this}setScalar(e){return this.x=e,this.y=e,this}setX(e){return this.x=e,this}setY(e){return this.y=e,this}setComponent(e,i){switch(e){case 0:this.x=i;break;case 1:this.y=i;break;default:throw new Error("THREE.Vector2: index is out of range: "+e)}return this}getComponent(e){switch(e){case 0:return this.x;case 1:return this.y;default:throw new Error("THREE.Vector2: index is out of range: "+e)}}clone(){return new this.constructor(this.x,this.y)}copy(e){return this.x=e.x,this.y=e.y,this}add(e){return this.x+=e.x,this.y+=e.y,this}addScalar(e){return this.x+=e,this.y+=e,this}addVectors(e,i){return this.x=e.x+i.x,this.y=e.y+i.y,this}addScaledVector(e,i){return this.x+=e.x*i,this.y+=e.y*i,this}sub(e){return this.x-=e.x,this.y-=e.y,this}subScalar(e){return this.x-=e,this.y-=e,this}subVectors(e,i){return this.x=e.x-i.x,this.y=e.y-i.y,this}multiply(e){return this.x*=e.x,this.y*=e.y,this}multiplyScalar(e){return this.x*=e,this.y*=e,this}divide(e){return this.x/=e.x,this.y/=e.y,this}divideScalar(e){return this.multiplyScalar(1/e)}applyMatrix3(e){const i=this.x,r=this.y,l=e.elements;return this.x=l[0]*i+l[3]*r+l[6],this.y=l[1]*i+l[4]*r+l[7],this}min(e){return this.x=Math.min(this.x,e.x),this.y=Math.min(this.y,e.y),this}max(e){return this.x=Math.max(this.x,e.x),this.y=Math.max(this.y,e.y),this}clamp(e,i){return this.x=vt(this.x,e.x,i.x),this.y=vt(this.y,e.y,i.y),this}clampScalar(e,i){return this.x=vt(this.x,e,i),this.y=vt(this.y,e,i),this}clampLength(e,i){const r=this.length();return this.divideScalar(r||1).multiplyScalar(vt(r,e,i))}floor(){return this.x=Math.floor(this.x),this.y=Math.floor(this.y),this}ceil(){return this.x=Math.ceil(this.x),this.y=Math.ceil(this.y),this}round(){return this.x=Math.round(this.x),this.y=Math.round(this.y),this}roundToZero(){return this.x=Math.trunc(this.x),this.y=Math.trunc(this.y),this}negate(){return this.x=-this.x,this.y=-this.y,this}dot(e){return this.x*e.x+this.y*e.y}cross(e){return this.x*e.y-this.y*e.x}lengthSq(){return this.x*this.x+this.y*this.y}length(){return Math.sqrt(this.x*this.x+this.y*this.y)}manhattanLength(){return Math.abs(this.x)+Math.abs(this.y)}normalize(){return this.divideScalar(this.length()||1)}angle(){return Math.atan2(-this.y,-this.x)+Math.PI}angleTo(e){const i=Math.sqrt(this.lengthSq()*e.lengthSq());if(i===0)return Math.PI/2;const r=this.dot(e)/i;return Math.acos(vt(r,-1,1))}distanceTo(e){return Math.sqrt(this.distanceToSquared(e))}distanceToSquared(e){const i=this.x-e.x,r=this.y-e.y;return i*i+r*r}manhattanDistanceTo(e){return Math.abs(this.x-e.x)+Math.abs(this.y-e.y)}setLength(e){return this.normalize().multiplyScalar(e)}lerp(e,i){return this.x+=(e.x-this.x)*i,this.y+=(e.y-this.y)*i,this}lerpVectors(e,i,r){return this.x=e.x+(i.x-e.x)*r,this.y=e.y+(i.y-e.y)*r,this}equals(e){return e.x===this.x&&e.y===this.y}fromArray(e,i=0){return this.x=e[i],this.y=e[i+1],this}toArray(e=[],i=0){return e[i]=this.x,e[i+1]=this.y,e}fromBufferAttribute(e,i){return this.x=e.getX(i),this.y=e.getY(i),this}rotateAround(e,i){const r=Math.cos(i),l=Math.sin(i),u=this.x-e.x,d=this.y-e.y;return this.x=u*r-d*l+e.x,this.y=u*l+d*r+e.y,this}random(){return this.x=Math.random(),this.y=Math.random(),this}*[Symbol.iterator](){yield this.x,yield this.y}};Jh.prototype.isVector2=!0;let Ut=Jh;class Wr{constructor(e=0,i=0,r=0,l=1){this.isQuaternion=!0,this._x=e,this._y=i,this._z=r,this._w=l}static slerpFlat(e,i,r,l,u,d,h){let m=r[l+0],p=r[l+1],v=r[l+2],x=r[l+3],g=u[d+0],E=u[d+1],T=u[d+2],D=u[d+3];if(x!==D||m!==g||p!==E||v!==T){let y=m*g+p*E+v*T+x*D;y<0&&(g=-g,E=-E,T=-T,D=-D,y=-y);let S=1-h;if(y<.9995){const z=Math.acos(y),F=Math.sin(z);S=Math.sin(S*z)/F,h=Math.sin(h*z)/F,m=m*S+g*h,p=p*S+E*h,v=v*S+T*h,x=x*S+D*h}else{m=m*S+g*h,p=p*S+E*h,v=v*S+T*h,x=x*S+D*h;const z=1/Math.sqrt(m*m+p*p+v*v+x*x);m*=z,p*=z,v*=z,x*=z}}e[i]=m,e[i+1]=p,e[i+2]=v,e[i+3]=x}static multiplyQuaternionsFlat(e,i,r,l,u,d){const h=r[l],m=r[l+1],p=r[l+2],v=r[l+3],x=u[d],g=u[d+1],E=u[d+2],T=u[d+3];return e[i]=h*T+v*x+m*E-p*g,e[i+1]=m*T+v*g+p*x-h*E,e[i+2]=p*T+v*E+h*g-m*x,e[i+3]=v*T-h*x-m*g-p*E,e}get x(){return this._x}set x(e){this._x=e,this._onChangeCallback()}get y(){return this._y}set y(e){this._y=e,this._onChangeCallback()}get z(){return this._z}set z(e){this._z=e,this._onChangeCallback()}get w(){return this._w}set w(e){this._w=e,this._onChangeCallback()}set(e,i,r,l){return this._x=e,this._y=i,this._z=r,this._w=l,this._onChangeCallback(),this}clone(){return new this.constructor(this._x,this._y,this._z,this._w)}copy(e){return this._x=e.x,this._y=e.y,this._z=e.z,this._w=e.w,this._onChangeCallback(),this}setFromEuler(e,i=!0){const r=e._x,l=e._y,u=e._z,d=e._order,h=Math.cos,m=Math.sin,p=h(r/2),v=h(l/2),x=h(u/2),g=m(r/2),E=m(l/2),T=m(u/2);switch(d){case"XYZ":this._x=g*v*x+p*E*T,this._y=p*E*x-g*v*T,this._z=p*v*T+g*E*x,this._w=p*v*x-g*E*T;break;case"YXZ":this._x=g*v*x+p*E*T,this._y=p*E*x-g*v*T,this._z=p*v*T-g*E*x,this._w=p*v*x+g*E*T;break;case"ZXY":this._x=g*v*x-p*E*T,this._y=p*E*x+g*v*T,this._z=p*v*T+g*E*x,this._w=p*v*x-g*E*T;break;case"ZYX":this._x=g*v*x-p*E*T,this._y=p*E*x+g*v*T,this._z=p*v*T-g*E*x,this._w=p*v*x+g*E*T;break;case"YZX":this._x=g*v*x+p*E*T,this._y=p*E*x+g*v*T,this._z=p*v*T-g*E*x,this._w=p*v*x-g*E*T;break;case"XZY":this._x=g*v*x-p*E*T,this._y=p*E*x-g*v*T,this._z=p*v*T+g*E*x,this._w=p*v*x+g*E*T;break;default:et("Quaternion: .setFromEuler() encountered an unknown order: "+d)}return i===!0&&this._onChangeCallback(),this}setFromAxisAngle(e,i){const r=i/2,l=Math.sin(r);return this._x=e.x*l,this._y=e.y*l,this._z=e.z*l,this._w=Math.cos(r),this._onChangeCallback(),this}setFromRotationMatrix(e){const i=e.elements,r=i[0],l=i[4],u=i[8],d=i[1],h=i[5],m=i[9],p=i[2],v=i[6],x=i[10],g=r+h+x;if(g>0){const E=.5/Math.sqrt(g+1);this._w=.25/E,this._x=(v-m)*E,this._y=(u-p)*E,this._z=(d-l)*E}else if(r>h&&r>x){const E=2*Math.sqrt(1+r-h-x);this._w=(v-m)/E,this._x=.25*E,this._y=(l+d)/E,this._z=(u+p)/E}else if(h>x){const E=2*Math.sqrt(1+h-r-x);this._w=(u-p)/E,this._x=(l+d)/E,this._y=.25*E,this._z=(m+v)/E}else{const E=2*Math.sqrt(1+x-r-h);this._w=(d-l)/E,this._x=(u+p)/E,this._y=(m+v)/E,this._z=.25*E}return this._onChangeCallback(),this}setFromUnitVectors(e,i){let r=e.dot(i)+1;return r<1e-8?(r=0,Math.abs(e.x)>Math.abs(e.z)?(this._x=-e.y,this._y=e.x,this._z=0,this._w=r):(this._x=0,this._y=-e.z,this._z=e.y,this._w=r)):(this._x=e.y*i.z-e.z*i.y,this._y=e.z*i.x-e.x*i.z,this._z=e.x*i.y-e.y*i.x,this._w=r),this.normalize()}angleTo(e){return 2*Math.acos(Math.abs(vt(this.dot(e),-1,1)))}rotateTowards(e,i){const r=this.angleTo(e);if(r===0)return this;const l=Math.min(1,i/r);return this.slerp(e,l),this}identity(){return this.set(0,0,0,1)}invert(){return this.conjugate()}conjugate(){return this._x*=-1,this._y*=-1,this._z*=-1,this._onChangeCallback(),this}dot(e){return this._x*e._x+this._y*e._y+this._z*e._z+this._w*e._w}lengthSq(){return this._x*this._x+this._y*this._y+this._z*this._z+this._w*this._w}length(){return Math.sqrt(this._x*this._x+this._y*this._y+this._z*this._z+this._w*this._w)}normalize(){let e=this.length();return e===0?(this._x=0,this._y=0,this._z=0,this._w=1):(e=1/e,this._x=this._x*e,this._y=this._y*e,this._z=this._z*e,this._w=this._w*e),this._onChangeCallback(),this}multiply(e){return this.multiplyQuaternions(this,e)}premultiply(e){return this.multiplyQuaternions(e,this)}multiplyQuaternions(e,i){const r=e._x,l=e._y,u=e._z,d=e._w,h=i._x,m=i._y,p=i._z,v=i._w;return this._x=r*v+d*h+l*p-u*m,this._y=l*v+d*m+u*h-r*p,this._z=u*v+d*p+r*m-l*h,this._w=d*v-r*h-l*m-u*p,this._onChangeCallback(),this}slerp(e,i){let r=e._x,l=e._y,u=e._z,d=e._w,h=this.dot(e);h<0&&(r=-r,l=-l,u=-u,d=-d,h=-h);let m=1-i;if(h<.9995){const p=Math.acos(h),v=Math.sin(p);m=Math.sin(m*p)/v,i=Math.sin(i*p)/v,this._x=this._x*m+r*i,this._y=this._y*m+l*i,this._z=this._z*m+u*i,this._w=this._w*m+d*i,this._onChangeCallback()}else this._x=this._x*m+r*i,this._y=this._y*m+l*i,this._z=this._z*m+u*i,this._w=this._w*m+d*i,this.normalize();return this}slerpQuaternions(e,i,r){return this.copy(e).slerp(i,r)}random(){const e=2*Math.PI*Math.random(),i=2*Math.PI*Math.random(),r=Math.random(),l=Math.sqrt(1-r),u=Math.sqrt(r);return this.set(l*Math.sin(e),l*Math.cos(e),u*Math.sin(i),u*Math.cos(i))}equals(e){return e._x===this._x&&e._y===this._y&&e._z===this._z&&e._w===this._w}fromArray(e,i=0){return this._x=e[i],this._y=e[i+1],this._z=e[i+2],this._w=e[i+3],this._onChangeCallback(),this}toArray(e=[],i=0){return e[i]=this._x,e[i+1]=this._y,e[i+2]=this._z,e[i+3]=this._w,e}fromBufferAttribute(e,i){return this._x=e.getX(i),this._y=e.getY(i),this._z=e.getZ(i),this._w=e.getW(i),this._onChangeCallback(),this}toJSON(){return this.toArray()}_onChange(e){return this._onChangeCallback=e,this}_onChangeCallback(){}*[Symbol.iterator](){yield this._x,yield this._y,yield this._z,yield this._w}}const $h=class $h{constructor(e=0,i=0,r=0){this.x=e,this.y=i,this.z=r}set(e,i,r){return r===void 0&&(r=this.z),this.x=e,this.y=i,this.z=r,this}setScalar(e){return this.x=e,this.y=e,this.z=e,this}setX(e){return this.x=e,this}setY(e){return this.y=e,this}setZ(e){return this.z=e,this}setComponent(e,i){switch(e){case 0:this.x=i;break;case 1:this.y=i;break;case 2:this.z=i;break;default:throw new Error("THREE.Vector3: index is out of range: "+e)}return this}getComponent(e){switch(e){case 0:return this.x;case 1:return this.y;case 2:return this.z;default:throw new Error("THREE.Vector3: index is out of range: "+e)}}clone(){return new this.constructor(this.x,this.y,this.z)}copy(e){return this.x=e.x,this.y=e.y,this.z=e.z,this}add(e){return this.x+=e.x,this.y+=e.y,this.z+=e.z,this}addScalar(e){return this.x+=e,this.y+=e,this.z+=e,this}addVectors(e,i){return this.x=e.x+i.x,this.y=e.y+i.y,this.z=e.z+i.z,this}addScaledVector(e,i){return this.x+=e.x*i,this.y+=e.y*i,this.z+=e.z*i,this}sub(e){return this.x-=e.x,this.y-=e.y,this.z-=e.z,this}subScalar(e){return this.x-=e,this.y-=e,this.z-=e,this}subVectors(e,i){return this.x=e.x-i.x,this.y=e.y-i.y,this.z=e.z-i.z,this}multiply(e){return this.x*=e.x,this.y*=e.y,this.z*=e.z,this}multiplyScalar(e){return this.x*=e,this.y*=e,this.z*=e,this}multiplyVectors(e,i){return this.x=e.x*i.x,this.y=e.y*i.y,this.z=e.z*i.z,this}applyEuler(e){return this.applyQuaternion(i_.setFromEuler(e))}applyAxisAngle(e,i){return this.applyQuaternion(i_.setFromAxisAngle(e,i))}applyMatrix3(e){const i=this.x,r=this.y,l=this.z,u=e.elements;return this.x=u[0]*i+u[3]*r+u[6]*l,this.y=u[1]*i+u[4]*r+u[7]*l,this.z=u[2]*i+u[5]*r+u[8]*l,this}applyNormalMatrix(e){return this.applyMatrix3(e).normalize()}applyMatrix4(e){const i=this.x,r=this.y,l=this.z,u=e.elements,d=1/(u[3]*i+u[7]*r+u[11]*l+u[15]);return this.x=(u[0]*i+u[4]*r+u[8]*l+u[12])*d,this.y=(u[1]*i+u[5]*r+u[9]*l+u[13])*d,this.z=(u[2]*i+u[6]*r+u[10]*l+u[14])*d,this}applyQuaternion(e){const i=this.x,r=this.y,l=this.z,u=e.x,d=e.y,h=e.z,m=e.w,p=2*(d*l-h*r),v=2*(h*i-u*l),x=2*(u*r-d*i);return this.x=i+m*p+d*x-h*v,this.y=r+m*v+h*p-u*x,this.z=l+m*x+u*v-d*p,this}project(e){return this.applyMatrix4(e.matrixWorldInverse).applyMatrix4(e.projectionMatrix)}unproject(e){return this.applyMatrix4(e.projectionMatrixInverse).applyMatrix4(e.matrixWorld)}transformDirection(e){const i=this.x,r=this.y,l=this.z,u=e.elements;return this.x=u[0]*i+u[4]*r+u[8]*l,this.y=u[1]*i+u[5]*r+u[9]*l,this.z=u[2]*i+u[6]*r+u[10]*l,this.normalize()}divide(e){return this.x/=e.x,this.y/=e.y,this.z/=e.z,this}divideScalar(e){return this.multiplyScalar(1/e)}min(e){return this.x=Math.min(this.x,e.x),this.y=Math.min(this.y,e.y),this.z=Math.min(this.z,e.z),this}max(e){return this.x=Math.max(this.x,e.x),this.y=Math.max(this.y,e.y),this.z=Math.max(this.z,e.z),this}clamp(e,i){return this.x=vt(this.x,e.x,i.x),this.y=vt(this.y,e.y,i.y),this.z=vt(this.z,e.z,i.z),this}clampScalar(e,i){return this.x=vt(this.x,e,i),this.y=vt(this.y,e,i),this.z=vt(this.z,e,i),this}clampLength(e,i){const r=this.length();return this.divideScalar(r||1).multiplyScalar(vt(r,e,i))}floor(){return this.x=Math.floor(this.x),this.y=Math.floor(this.y),this.z=Math.floor(this.z),this}ceil(){return this.x=Math.ceil(this.x),this.y=Math.ceil(this.y),this.z=Math.ceil(this.z),this}round(){return this.x=Math.round(this.x),this.y=Math.round(this.y),this.z=Math.round(this.z),this}roundToZero(){return this.x=Math.trunc(this.x),this.y=Math.trunc(this.y),this.z=Math.trunc(this.z),this}negate(){return this.x=-this.x,this.y=-this.y,this.z=-this.z,this}dot(e){return this.x*e.x+this.y*e.y+this.z*e.z}lengthSq(){return this.x*this.x+this.y*this.y+this.z*this.z}length(){return Math.sqrt(this.x*this.x+this.y*this.y+this.z*this.z)}manhattanLength(){return Math.abs(this.x)+Math.abs(this.y)+Math.abs(this.z)}normalize(){return this.divideScalar(this.length()||1)}setLength(e){return this.normalize().multiplyScalar(e)}lerp(e,i){return this.x+=(e.x-this.x)*i,this.y+=(e.y-this.y)*i,this.z+=(e.z-this.z)*i,this}lerpVectors(e,i,r){return this.x=e.x+(i.x-e.x)*r,this.y=e.y+(i.y-e.y)*r,this.z=e.z+(i.z-e.z)*r,this}cross(e){return this.crossVectors(this,e)}crossVectors(e,i){const r=e.x,l=e.y,u=e.z,d=i.x,h=i.y,m=i.z;return this.x=l*m-u*h,this.y=u*d-r*m,this.z=r*h-l*d,this}projectOnVector(e){const i=e.lengthSq();if(i===0)return this.set(0,0,0);const r=e.dot(this)/i;return this.copy(e).multiplyScalar(r)}projectOnPlane(e){return md.copy(this).projectOnVector(e),this.sub(md)}reflect(e){return this.sub(md.copy(e).multiplyScalar(2*this.dot(e)))}angleTo(e){const i=Math.sqrt(this.lengthSq()*e.lengthSq());if(i===0)return Math.PI/2;const r=this.dot(e)/i;return Math.acos(vt(r,-1,1))}distanceTo(e){return Math.sqrt(this.distanceToSquared(e))}distanceToSquared(e){const i=this.x-e.x,r=this.y-e.y,l=this.z-e.z;return i*i+r*r+l*l}manhattanDistanceTo(e){return Math.abs(this.x-e.x)+Math.abs(this.y-e.y)+Math.abs(this.z-e.z)}setFromSpherical(e){return this.setFromSphericalCoords(e.radius,e.phi,e.theta)}setFromSphericalCoords(e,i,r){const l=Math.sin(i)*e;return this.x=l*Math.sin(r),this.y=Math.cos(i)*e,this.z=l*Math.cos(r),this}setFromCylindrical(e){return this.setFromCylindricalCoords(e.radius,e.theta,e.y)}setFromCylindricalCoords(e,i,r){return this.x=e*Math.sin(i),this.y=r,this.z=e*Math.cos(i),this}setFromMatrixPosition(e){const i=e.elements;return this.x=i[12],this.y=i[13],this.z=i[14],this}setFromMatrixScale(e){const i=this.setFromMatrixColumn(e,0).length(),r=this.setFromMatrixColumn(e,1).length(),l=this.setFromMatrixColumn(e,2).length();return this.x=i,this.y=r,this.z=l,this}setFromMatrixColumn(e,i){return this.fromArray(e.elements,i*4)}setFromMatrix3Column(e,i){return this.fromArray(e.elements,i*3)}setFromEuler(e){return this.x=e._x,this.y=e._y,this.z=e._z,this}setFromColor(e){return this.x=e.r,this.y=e.g,this.z=e.b,this}equals(e){return e.x===this.x&&e.y===this.y&&e.z===this.z}fromArray(e,i=0){return this.x=e[i],this.y=e[i+1],this.z=e[i+2],this}toArray(e=[],i=0){return e[i]=this.x,e[i+1]=this.y,e[i+2]=this.z,e}fromBufferAttribute(e,i){return this.x=e.getX(i),this.y=e.getY(i),this.z=e.getZ(i),this}random(){return this.x=Math.random(),this.y=Math.random(),this.z=Math.random(),this}randomDirection(){const e=Math.random()*Math.PI*2,i=Math.random()*2-1,r=Math.sqrt(1-i*i);return this.x=r*Math.cos(e),this.y=i,this.z=r*Math.sin(e),this}*[Symbol.iterator](){yield this.x,yield this.y,yield this.z}};$h.prototype.isVector3=!0;let ae=$h;const md=new ae,i_=new Wr,jh=class jh{constructor(e,i,r,l,u,d,h,m,p){this.elements=[1,0,0,0,1,0,0,0,1],e!==void 0&&this.set(e,i,r,l,u,d,h,m,p)}set(e,i,r,l,u,d,h,m,p){const v=this.elements;return v[0]=e,v[1]=l,v[2]=h,v[3]=i,v[4]=u,v[5]=m,v[6]=r,v[7]=d,v[8]=p,this}identity(){return this.set(1,0,0,0,1,0,0,0,1),this}copy(e){const i=this.elements,r=e.elements;return i[0]=r[0],i[1]=r[1],i[2]=r[2],i[3]=r[3],i[4]=r[4],i[5]=r[5],i[6]=r[6],i[7]=r[7],i[8]=r[8],this}extractBasis(e,i,r){return e.setFromMatrix3Column(this,0),i.setFromMatrix3Column(this,1),r.setFromMatrix3Column(this,2),this}setFromMatrix4(e){const i=e.elements;return this.set(i[0],i[4],i[8],i[1],i[5],i[9],i[2],i[6],i[10]),this}multiply(e){return this.multiplyMatrices(this,e)}premultiply(e){return this.multiplyMatrices(e,this)}multiplyMatrices(e,i){const r=e.elements,l=i.elements,u=this.elements,d=r[0],h=r[3],m=r[6],p=r[1],v=r[4],x=r[7],g=r[2],E=r[5],T=r[8],D=l[0],y=l[3],S=l[6],z=l[1],F=l[4],C=l[7],O=l[2],U=l[5],B=l[8];return u[0]=d*D+h*z+m*O,u[3]=d*y+h*F+m*U,u[6]=d*S+h*C+m*B,u[1]=p*D+v*z+x*O,u[4]=p*y+v*F+x*U,u[7]=p*S+v*C+x*B,u[2]=g*D+E*z+T*O,u[5]=g*y+E*F+T*U,u[8]=g*S+E*C+T*B,this}multiplyScalar(e){const i=this.elements;return i[0]*=e,i[3]*=e,i[6]*=e,i[1]*=e,i[4]*=e,i[7]*=e,i[2]*=e,i[5]*=e,i[8]*=e,this}determinant(){const e=this.elements,i=e[0],r=e[1],l=e[2],u=e[3],d=e[4],h=e[5],m=e[6],p=e[7],v=e[8];return i*d*v-i*h*p-r*u*v+r*h*m+l*u*p-l*d*m}invert(){const e=this.elements,i=e[0],r=e[1],l=e[2],u=e[3],d=e[4],h=e[5],m=e[6],p=e[7],v=e[8],x=v*d-h*p,g=h*m-v*u,E=p*u-d*m,T=i*x+r*g+l*E;if(T===0)return this.set(0,0,0,0,0,0,0,0,0);const D=1/T;return e[0]=x*D,e[1]=(l*p-v*r)*D,e[2]=(h*r-l*d)*D,e[3]=g*D,e[4]=(v*i-l*m)*D,e[5]=(l*u-h*i)*D,e[6]=E*D,e[7]=(r*m-p*i)*D,e[8]=(d*i-r*u)*D,this}transpose(){let e;const i=this.elements;return e=i[1],i[1]=i[3],i[3]=e,e=i[2],i[2]=i[6],i[6]=e,e=i[5],i[5]=i[7],i[7]=e,this}getNormalMatrix(e){return this.setFromMatrix4(e).invert().transpose()}transposeIntoArray(e){const i=this.elements;return e[0]=i[0],e[1]=i[3],e[2]=i[6],e[3]=i[1],e[4]=i[4],e[5]=i[7],e[6]=i[2],e[7]=i[5],e[8]=i[8],this}setUvTransform(e,i,r,l,u,d,h){const m=Math.cos(u),p=Math.sin(u);return this.set(r*m,r*p,-r*(m*d+p*h)+d+e,-l*p,l*m,-l*(-p*d+m*h)+h+i,0,0,1),this}scale(e,i){return Br("Matrix3: .scale() is deprecated. Use .makeScale() instead."),this.premultiply(gd.makeScale(e,i)),this}rotate(e){return Br("Matrix3: .rotate() is deprecated. Use .makeRotation() instead."),this.premultiply(gd.makeRotation(-e)),this}translate(e,i){return Br("Matrix3: .translate() is deprecated. Use .makeTranslation() instead."),this.premultiply(gd.makeTranslation(e,i)),this}makeTranslation(e,i){return e.isVector2?this.set(1,0,e.x,0,1,e.y,0,0,1):this.set(1,0,e,0,1,i,0,0,1),this}makeRotation(e){const i=Math.cos(e),r=Math.sin(e);return this.set(i,-r,0,r,i,0,0,0,1),this}makeScale(e,i){return this.set(e,0,0,0,i,0,0,0,1),this}equals(e){const i=this.elements,r=e.elements;for(let l=0;l<9;l++)if(i[l]!==r[l])return!1;return!0}fromArray(e,i=0){for(let r=0;r<9;r++)this.elements[r]=e[r+i];return this}toArray(e=[],i=0){const r=this.elements;return e[i]=r[0],e[i+1]=r[1],e[i+2]=r[2],e[i+3]=r[3],e[i+4]=r[4],e[i+5]=r[5],e[i+6]=r[6],e[i+7]=r[7],e[i+8]=r[8],e}clone(){return new this.constructor().fromArray(this.elements)}};jh.prototype.isMatrix3=!0;let st=jh;const gd=new st,a_=new st().set(.4123908,.3575843,.1804808,.212639,.7151687,.0721923,.0193308,.1191948,.9505322),s_=new st().set(3.2409699,-1.5373832,-.4986108,-.9692436,1.8759675,.0415551,.0556301,-.203977,1.0569715);function Qy(){const s={enabled:!0,workingColorSpace:Kc,spaces:{},convert:function(l,u,d){return this.enabled===!1||u===d||!u||!d||(this.spaces[u].transfer===It&&(l.r=ya(l.r),l.g=ya(l.g),l.b=ya(l.b)),this.spaces[u].primaries!==this.spaces[d].primaries&&(l.applyMatrix3(this.spaces[u].toXYZ),l.applyMatrix3(this.spaces[d].fromXYZ)),this.spaces[d].transfer===It&&(l.r=Ir(l.r),l.g=Ir(l.g),l.b=Ir(l.b))),l},workingToColorSpace:function(l,u){return this.convert(l,this.workingColorSpace,u)},colorSpaceToWorking:function(l,u){return this.convert(l,u,this.workingColorSpace)},getPrimaries:function(l){return this.spaces[l].primaries},getTransfer:function(l){return l===rs?Qc:this.spaces[l].transfer},getToneMappingMode:function(l){return this.spaces[l].outputColorSpaceConfig.toneMappingMode||"standard"},getLuminanceCoefficients:function(l,u=this.workingColorSpace){return l.fromArray(this.spaces[u].luminanceCoefficients)},define:function(l){Object.assign(this.spaces,l)},_getMatrix:function(l,u,d){return l.copy(this.spaces[u].toXYZ).multiply(this.spaces[d].fromXYZ)},_getDrawingBufferColorSpace:function(l){return this.spaces[l].outputColorSpaceConfig.drawingBufferColorSpace},_getUnpackColorSpace:function(l=this.workingColorSpace){return this.spaces[l].workingColorSpaceConfig.unpackColorSpace},fromWorkingColorSpace:function(l,u){return Br("ColorManagement: .fromWorkingColorSpace() has been renamed to .workingToColorSpace()."),s.workingToColorSpace(l,u)},toWorkingColorSpace:function(l,u){return Br("ColorManagement: .toWorkingColorSpace() has been renamed to .colorSpaceToWorking()."),s.colorSpaceToWorking(l,u)}},e=[.64,.33,.3,.6,.15,.06],i=[.2126,.7152,.0722],r=[.3127,.329];return s.define({[Kc]:{primaries:e,whitePoint:r,transfer:Qc,toXYZ:a_,fromXYZ:s_,luminanceCoefficients:i,workingColorSpaceConfig:{unpackColorSpace:_i},outputColorSpaceConfig:{drawingBufferColorSpace:_i}},[_i]:{primaries:e,whitePoint:r,transfer:It,toXYZ:a_,fromXYZ:s_,luminanceCoefficients:i,outputColorSpaceConfig:{drawingBufferColorSpace:_i}}}),s}const yt=Qy();function ya(s){return s<.04045?s*.0773993808:Math.pow(s*.9478672986+.0521327014,2.4)}function Ir(s){return s<.0031308?s*12.92:1.055*Math.pow(s,.41666)-.055}let yr;class Jy{static getDataURL(e,i="image/png"){if(/^data:/i.test(e.src)||typeof HTMLCanvasElement>"u")return e.src;let r;if(e instanceof HTMLCanvasElement)r=e;else{yr===void 0&&(yr=$c("canvas")),yr.width=e.width,yr.height=e.height;const l=yr.getContext("2d");e instanceof ImageData?l.putImageData(e,0,0):l.drawImage(e,0,0,e.width,e.height),r=yr}return r.toDataURL(i)}static sRGBToLinear(e){if(typeof HTMLImageElement<"u"&&e instanceof HTMLImageElement||typeof HTMLCanvasElement<"u"&&e instanceof HTMLCanvasElement||typeof ImageBitmap<"u"&&e instanceof ImageBitmap){const i=$c("canvas");i.width=e.width,i.height=e.height;const r=i.getContext("2d");r.drawImage(e,0,0,e.width,e.height);const l=r.getImageData(0,0,e.width,e.height),u=l.data;for(let d=0;d<u.length;d++)u[d]=ya(u[d]/255)*255;return r.putImageData(l,0,0),i}else if(e.data){const i=e.data.slice(0);for(let r=0;r<i.length;r++)i instanceof Uint8Array||i instanceof Uint8ClampedArray?i[r]=Math.floor(ya(i[r]/255)*255):i[r]=ya(i[r]);return{data:i,width:e.width,height:e.height}}else return et("ImageUtils.sRGBToLinear(): Unsupported image type. No color space conversion applied."),e}}let $y=0;class Zh{constructor(e=null){this.isSource=!0,Object.defineProperty(this,"id",{value:$y++}),this.uuid=Xr(),this.data=e,this.dataReady=!0,this.version=0}getSize(e){const i=this.data;return typeof HTMLVideoElement<"u"&&i instanceof HTMLVideoElement?e.set(i.videoWidth,i.videoHeight,0):typeof VideoFrame<"u"&&i instanceof VideoFrame?e.set(i.displayWidth,i.displayHeight,0):i!==null?e.set(i.width,i.height,i.depth||0):e.set(0,0,0),e}set needsUpdate(e){e===!0&&this.version++}toJSON(e){const i=e===void 0||typeof e=="string";if(!i&&e.images[this.uuid]!==void 0)return e.images[this.uuid];const r={uuid:this.uuid,url:""},l=this.data;if(l!==null){let u;if(Array.isArray(l)){u=[];for(let d=0,h=l.length;d<h;d++)l[d].isDataTexture?u.push(_d(l[d].image)):u.push(_d(l[d]))}else u=_d(l);r.url=u}return i||(e.images[this.uuid]=r),r}}function _d(s){return typeof HTMLImageElement<"u"&&s instanceof HTMLImageElement||typeof HTMLCanvasElement<"u"&&s instanceof HTMLCanvasElement||typeof ImageBitmap<"u"&&s instanceof ImageBitmap?Jy.getDataURL(s):s.data?{data:Array.from(s.data),width:s.width,height:s.height,type:s.data.constructor.name}:(et("Texture: Unable to serialize Texture."),{})}let jy=0;const vd=new ae;class Bn extends Vs{constructor(e=Bn.DEFAULT_IMAGE,i=Bn.DEFAULT_MAPPING,r=Sa,l=Sa,u=On,d=Fs,h=Di,m=xi,p=Bn.DEFAULT_ANISOTROPY,v=rs){super(),this.isTexture=!0,Object.defineProperty(this,"id",{value:jy++}),this.uuid=Xr(),this.name="",this.source=new Zh(e),this.mipmaps=[],this.mapping=i,this.channel=0,this.wrapS=r,this.wrapT=l,this.magFilter=u,this.minFilter=d,this.anisotropy=p,this.format=h,this.internalFormat=null,this.type=m,this.offset=new Ut(0,0),this.repeat=new Ut(1,1),this.center=new Ut(0,0),this.rotation=0,this.matrixAutoUpdate=!0,this.matrix=new st,this.generateMipmaps=!0,this.premultiplyAlpha=!1,this.flipY=!0,this.unpackAlignment=4,this.colorSpace=v,this.userData={},this.updateRanges=[],this.version=0,this.onUpdate=null,this.renderTarget=null,this.isRenderTargetTexture=!1,this.isArrayTexture=!!(e&&e.depth&&e.depth>1),this.pmremVersion=0,this.normalized=!1}get width(){return this.source.getSize(vd).x}get height(){return this.source.getSize(vd).y}get depth(){return this.source.getSize(vd).z}get image(){return this.source.data}set image(e){this.source.data=e}updateMatrix(){this.matrix.setUvTransform(this.offset.x,this.offset.y,this.repeat.x,this.repeat.y,this.rotation,this.center.x,this.center.y)}addUpdateRange(e,i){this.updateRanges.push({start:e,count:i})}clearUpdateRanges(){this.updateRanges.length=0}clone(){return new this.constructor().copy(this)}copy(e){return this.name=e.name,this.source=e.source,this.mipmaps=e.mipmaps.slice(0),this.mapping=e.mapping,this.channel=e.channel,this.wrapS=e.wrapS,this.wrapT=e.wrapT,this.magFilter=e.magFilter,this.minFilter=e.minFilter,this.anisotropy=e.anisotropy,this.format=e.format,this.internalFormat=e.internalFormat,this.type=e.type,this.normalized=e.normalized,this.offset.copy(e.offset),this.repeat.copy(e.repeat),this.center.copy(e.center),this.rotation=e.rotation,this.matrixAutoUpdate=e.matrixAutoUpdate,this.matrix.copy(e.matrix),this.generateMipmaps=e.generateMipmaps,this.premultiplyAlpha=e.premultiplyAlpha,this.flipY=e.flipY,this.unpackAlignment=e.unpackAlignment,this.colorSpace=e.colorSpace,this.renderTarget=e.renderTarget,this.isRenderTargetTexture=e.isRenderTargetTexture,this.isArrayTexture=e.isArrayTexture,this.userData=JSON.parse(JSON.stringify(e.userData)),this.needsUpdate=!0,this}setValues(e){for(const i in e){const r=e[i];if(r===void 0){et(`Texture.setValues(): parameter '${i}' has value of undefined.`);continue}const l=this[i];if(l===void 0){et(`Texture.setValues(): property '${i}' does not exist.`);continue}l&&r&&l.isVector2&&r.isVector2||l&&r&&l.isVector3&&r.isVector3||l&&r&&l.isMatrix3&&r.isMatrix3?l.copy(r):this[i]=r}}toJSON(e){const i=e===void 0||typeof e=="string";if(!i&&e.textures[this.uuid]!==void 0)return e.textures[this.uuid];const r={metadata:{version:4.7,type:"Texture",generator:"Texture.toJSON"},uuid:this.uuid,name:this.name,image:this.source.toJSON(e).uuid,mapping:this.mapping,channel:this.channel,repeat:[this.repeat.x,this.repeat.y],offset:[this.offset.x,this.offset.y],center:[this.center.x,this.center.y],rotation:this.rotation,wrap:[this.wrapS,this.wrapT],format:this.format,internalFormat:this.internalFormat,type:this.type,normalized:this.normalized,colorSpace:this.colorSpace,minFilter:this.minFilter,magFilter:this.magFilter,anisotropy:this.anisotropy,flipY:this.flipY,generateMipmaps:this.generateMipmaps,premultiplyAlpha:this.premultiplyAlpha,unpackAlignment:this.unpackAlignment};return Object.keys(this.userData).length>0&&(r.userData=this.userData),i||(e.textures[this.uuid]=r),r}dispose(){this.dispatchEvent({type:"dispose"})}transformUv(e){if(this.mapping!==uv)return e;if(e.applyMatrix3(this.matrix),e.x<0||e.x>1)switch(this.wrapS){case ih:e.x=e.x-Math.floor(e.x);break;case Sa:e.x=e.x<0?0:1;break;case ah:Math.abs(Math.floor(e.x)%2)===1?e.x=Math.ceil(e.x)-e.x:e.x=e.x-Math.floor(e.x);break}if(e.y<0||e.y>1)switch(this.wrapT){case ih:e.y=e.y-Math.floor(e.y);break;case Sa:e.y=e.y<0?0:1;break;case ah:Math.abs(Math.floor(e.y)%2)===1?e.y=Math.ceil(e.y)-e.y:e.y=e.y-Math.floor(e.y);break}return this.flipY&&(e.y=1-e.y),e}set needsUpdate(e){e===!0&&(this.version++,this.source.needsUpdate=!0)}set needsPMREMUpdate(e){e===!0&&this.pmremVersion++}}Bn.DEFAULT_IMAGE=null;Bn.DEFAULT_MAPPING=uv;Bn.DEFAULT_ANISOTROPY=1;const ep=class ep{constructor(e=0,i=0,r=0,l=1){this.x=e,this.y=i,this.z=r,this.w=l}get width(){return this.z}set width(e){this.z=e}get height(){return this.w}set height(e){this.w=e}set(e,i,r,l){return this.x=e,this.y=i,this.z=r,this.w=l,this}setScalar(e){return this.x=e,this.y=e,this.z=e,this.w=e,this}setX(e){return this.x=e,this}setY(e){return this.y=e,this}setZ(e){return this.z=e,this}setW(e){return this.w=e,this}setComponent(e,i){switch(e){case 0:this.x=i;break;case 1:this.y=i;break;case 2:this.z=i;break;case 3:this.w=i;break;default:throw new Error("THREE.Vector4: index is out of range: "+e)}return this}getComponent(e){switch(e){case 0:return this.x;case 1:return this.y;case 2:return this.z;case 3:return this.w;default:throw new Error("THREE.Vector4: index is out of range: "+e)}}clone(){return new this.constructor(this.x,this.y,this.z,this.w)}copy(e){return this.x=e.x,this.y=e.y,this.z=e.z,this.w=e.w!==void 0?e.w:1,this}add(e){return this.x+=e.x,this.y+=e.y,this.z+=e.z,this.w+=e.w,this}addScalar(e){return this.x+=e,this.y+=e,this.z+=e,this.w+=e,this}addVectors(e,i){return this.x=e.x+i.x,this.y=e.y+i.y,this.z=e.z+i.z,this.w=e.w+i.w,this}addScaledVector(e,i){return this.x+=e.x*i,this.y+=e.y*i,this.z+=e.z*i,this.w+=e.w*i,this}sub(e){return this.x-=e.x,this.y-=e.y,this.z-=e.z,this.w-=e.w,this}subScalar(e){return this.x-=e,this.y-=e,this.z-=e,this.w-=e,this}subVectors(e,i){return this.x=e.x-i.x,this.y=e.y-i.y,this.z=e.z-i.z,this.w=e.w-i.w,this}multiply(e){return this.x*=e.x,this.y*=e.y,this.z*=e.z,this.w*=e.w,this}multiplyScalar(e){return this.x*=e,this.y*=e,this.z*=e,this.w*=e,this}applyMatrix4(e){const i=this.x,r=this.y,l=this.z,u=this.w,d=e.elements;return this.x=d[0]*i+d[4]*r+d[8]*l+d[12]*u,this.y=d[1]*i+d[5]*r+d[9]*l+d[13]*u,this.z=d[2]*i+d[6]*r+d[10]*l+d[14]*u,this.w=d[3]*i+d[7]*r+d[11]*l+d[15]*u,this}divide(e){return this.x/=e.x,this.y/=e.y,this.z/=e.z,this.w/=e.w,this}divideScalar(e){return this.multiplyScalar(1/e)}setAxisAngleFromQuaternion(e){this.w=2*Math.acos(e.w);const i=Math.sqrt(1-e.w*e.w);return i<1e-4?(this.x=1,this.y=0,this.z=0):(this.x=e.x/i,this.y=e.y/i,this.z=e.z/i),this}setAxisAngleFromRotationMatrix(e){let i,r,l,u;const m=e.elements,p=m[0],v=m[4],x=m[8],g=m[1],E=m[5],T=m[9],D=m[2],y=m[6],S=m[10];if(Math.abs(v-g)<.01&&Math.abs(x-D)<.01&&Math.abs(T-y)<.01){if(Math.abs(v+g)<.1&&Math.abs(x+D)<.1&&Math.abs(T+y)<.1&&Math.abs(p+E+S-3)<.1)return this.set(1,0,0,0),this;i=Math.PI;const F=(p+1)/2,C=(E+1)/2,O=(S+1)/2,U=(v+g)/4,B=(x+D)/4,b=(T+y)/4;return F>C&&F>O?F<.01?(r=0,l=.707106781,u=.707106781):(r=Math.sqrt(F),l=U/r,u=B/r):C>O?C<.01?(r=.707106781,l=0,u=.707106781):(l=Math.sqrt(C),r=U/l,u=b/l):O<.01?(r=.707106781,l=.707106781,u=0):(u=Math.sqrt(O),r=B/u,l=b/u),this.set(r,l,u,i),this}let z=Math.sqrt((y-T)*(y-T)+(x-D)*(x-D)+(g-v)*(g-v));return Math.abs(z)<.001&&(z=1),this.x=(y-T)/z,this.y=(x-D)/z,this.z=(g-v)/z,this.w=Math.acos((p+E+S-1)/2),this}setFromMatrixPosition(e){const i=e.elements;return this.x=i[12],this.y=i[13],this.z=i[14],this.w=i[15],this}min(e){return this.x=Math.min(this.x,e.x),this.y=Math.min(this.y,e.y),this.z=Math.min(this.z,e.z),this.w=Math.min(this.w,e.w),this}max(e){return this.x=Math.max(this.x,e.x),this.y=Math.max(this.y,e.y),this.z=Math.max(this.z,e.z),this.w=Math.max(this.w,e.w),this}clamp(e,i){return this.x=vt(this.x,e.x,i.x),this.y=vt(this.y,e.y,i.y),this.z=vt(this.z,e.z,i.z),this.w=vt(this.w,e.w,i.w),this}clampScalar(e,i){return this.x=vt(this.x,e,i),this.y=vt(this.y,e,i),this.z=vt(this.z,e,i),this.w=vt(this.w,e,i),this}clampLength(e,i){const r=this.length();return this.divideScalar(r||1).multiplyScalar(vt(r,e,i))}floor(){return this.x=Math.floor(this.x),this.y=Math.floor(this.y),this.z=Math.floor(this.z),this.w=Math.floor(this.w),this}ceil(){return this.x=Math.ceil(this.x),this.y=Math.ceil(this.y),this.z=Math.ceil(this.z),this.w=Math.ceil(this.w),this}round(){return this.x=Math.round(this.x),this.y=Math.round(this.y),this.z=Math.round(this.z),this.w=Math.round(this.w),this}roundToZero(){return this.x=Math.trunc(this.x),this.y=Math.trunc(this.y),this.z=Math.trunc(this.z),this.w=Math.trunc(this.w),this}negate(){return this.x=-this.x,this.y=-this.y,this.z=-this.z,this.w=-this.w,this}dot(e){return this.x*e.x+this.y*e.y+this.z*e.z+this.w*e.w}lengthSq(){return this.x*this.x+this.y*this.y+this.z*this.z+this.w*this.w}length(){return Math.sqrt(this.x*this.x+this.y*this.y+this.z*this.z+this.w*this.w)}manhattanLength(){return Math.abs(this.x)+Math.abs(this.y)+Math.abs(this.z)+Math.abs(this.w)}normalize(){return this.divideScalar(this.length()||1)}setLength(e){return this.normalize().multiplyScalar(e)}lerp(e,i){return this.x+=(e.x-this.x)*i,this.y+=(e.y-this.y)*i,this.z+=(e.z-this.z)*i,this.w+=(e.w-this.w)*i,this}lerpVectors(e,i,r){return this.x=e.x+(i.x-e.x)*r,this.y=e.y+(i.y-e.y)*r,this.z=e.z+(i.z-e.z)*r,this.w=e.w+(i.w-e.w)*r,this}equals(e){return e.x===this.x&&e.y===this.y&&e.z===this.z&&e.w===this.w}fromArray(e,i=0){return this.x=e[i],this.y=e[i+1],this.z=e[i+2],this.w=e[i+3],this}toArray(e=[],i=0){return e[i]=this.x,e[i+1]=this.y,e[i+2]=this.z,e[i+3]=this.w,e}fromBufferAttribute(e,i){return this.x=e.getX(i),this.y=e.getY(i),this.z=e.getZ(i),this.w=e.getW(i),this}random(){return this.x=Math.random(),this.y=Math.random(),this.z=Math.random(),this.w=Math.random(),this}*[Symbol.iterator](){yield this.x,yield this.y,yield this.z,yield this.w}};ep.prototype.isVector4=!0;let on=ep;class eE extends Vs{constructor(e=1,i=1,r={}){super(),r=Object.assign({generateMipmaps:!1,internalFormat:null,minFilter:On,depthBuffer:!0,stencilBuffer:!1,resolveDepthBuffer:!0,resolveStencilBuffer:!0,depthTexture:null,samples:0,count:1,depth:1,multiview:!1,useArrayDepthTexture:!1},r),this.isRenderTarget=!0,this.width=e,this.height=i,this.depth=r.depth,this.scissor=new on(0,0,e,i),this.scissorTest=!1,this.viewport=new on(0,0,e,i),this.textures=[];const l={width:e,height:i,depth:r.depth},u=new Bn(l),d=r.count;for(let h=0;h<d;h++)this.textures[h]=u.clone(),this.textures[h].isRenderTargetTexture=!0,this.textures[h].renderTarget=this;this._setTextureOptions(r),this.depthBuffer=r.depthBuffer,this.stencilBuffer=r.stencilBuffer,this.resolveDepthBuffer=r.resolveDepthBuffer,this.resolveStencilBuffer=r.resolveStencilBuffer,this._depthTexture=null,this.depthTexture=r.depthTexture,this.samples=r.samples,this.multiview=r.multiview,this.useArrayDepthTexture=r.useArrayDepthTexture}_setTextureOptions(e={}){const i={minFilter:On,generateMipmaps:!1,flipY:!1,internalFormat:null};e.mapping!==void 0&&(i.mapping=e.mapping),e.wrapS!==void 0&&(i.wrapS=e.wrapS),e.wrapT!==void 0&&(i.wrapT=e.wrapT),e.wrapR!==void 0&&(i.wrapR=e.wrapR),e.magFilter!==void 0&&(i.magFilter=e.magFilter),e.minFilter!==void 0&&(i.minFilter=e.minFilter),e.format!==void 0&&(i.format=e.format),e.type!==void 0&&(i.type=e.type),e.anisotropy!==void 0&&(i.anisotropy=e.anisotropy),e.colorSpace!==void 0&&(i.colorSpace=e.colorSpace),e.flipY!==void 0&&(i.flipY=e.flipY),e.generateMipmaps!==void 0&&(i.generateMipmaps=e.generateMipmaps),e.internalFormat!==void 0&&(i.internalFormat=e.internalFormat);for(let r=0;r<this.textures.length;r++)this.textures[r].setValues(i)}get texture(){return this.textures[0]}set texture(e){this.textures[0]=e}set depthTexture(e){this._depthTexture!==null&&(this._depthTexture.renderTarget=null),e!==null&&(e.renderTarget=this),this._depthTexture=e}get depthTexture(){return this._depthTexture}setSize(e,i,r=1){if(this.width!==e||this.height!==i||this.depth!==r){this.width=e,this.height=i,this.depth=r;for(let l=0,u=this.textures.length;l<u;l++)this.textures[l].image.width=e,this.textures[l].image.height=i,this.textures[l].image.depth=r,this.textures[l].isData3DTexture!==!0&&(this.textures[l].isArrayTexture=this.textures[l].image.depth>1);this.dispose()}this.viewport.set(0,0,e,i),this.scissor.set(0,0,e,i)}clone(){return new this.constructor().copy(this)}copy(e){this.width=e.width,this.height=e.height,this.depth=e.depth,this.scissor.copy(e.scissor),this.scissorTest=e.scissorTest,this.viewport.copy(e.viewport),this.textures.length=0;for(let i=0,r=e.textures.length;i<r;i++){this.textures[i]=e.textures[i].clone(),this.textures[i].isRenderTargetTexture=!0,this.textures[i].renderTarget=this;const l=Object.assign({},e.textures[i].image);this.textures[i].source=new Zh(l)}return this.depthBuffer=e.depthBuffer,this.stencilBuffer=e.stencilBuffer,this.resolveDepthBuffer=e.resolveDepthBuffer,this.resolveStencilBuffer=e.resolveStencilBuffer,e.depthTexture!==null&&(this.depthTexture=e.depthTexture.clone()),this.samples=e.samples,this.multiview=e.multiview,this.useArrayDepthTexture=e.useArrayDepthTexture,this}dispose(){this.dispatchEvent({type:"dispose"})}}class Wi extends eE{constructor(e=1,i=1,r={}){super(e,i,r),this.isWebGLRenderTarget=!0}}class xv extends Bn{constructor(e=null,i=1,r=1,l=1){super(null),this.isDataArrayTexture=!0,this.image={data:e,width:i,height:r,depth:l},this.magFilter=Dn,this.minFilter=Dn,this.wrapR=Sa,this.generateMipmaps=!1,this.flipY=!1,this.unpackAlignment=1,this.layerUpdates=new Set}addLayerUpdate(e){this.layerUpdates.add(e)}clearLayerUpdates(){this.layerUpdates.clear()}}class tE extends Bn{constructor(e=null,i=1,r=1,l=1){super(null),this.isData3DTexture=!0,this.image={data:e,width:i,height:r,depth:l},this.magFilter=Dn,this.minFilter=Dn,this.wrapR=Sa,this.generateMipmaps=!1,this.flipY=!1,this.unpackAlignment=1}}const jc=class jc{constructor(e,i,r,l,u,d,h,m,p,v,x,g,E,T,D,y){this.elements=[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1],e!==void 0&&this.set(e,i,r,l,u,d,h,m,p,v,x,g,E,T,D,y)}set(e,i,r,l,u,d,h,m,p,v,x,g,E,T,D,y){const S=this.elements;return S[0]=e,S[4]=i,S[8]=r,S[12]=l,S[1]=u,S[5]=d,S[9]=h,S[13]=m,S[2]=p,S[6]=v,S[10]=x,S[14]=g,S[3]=E,S[7]=T,S[11]=D,S[15]=y,this}identity(){return this.set(1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1),this}clone(){return new jc().fromArray(this.elements)}copy(e){const i=this.elements,r=e.elements;return i[0]=r[0],i[1]=r[1],i[2]=r[2],i[3]=r[3],i[4]=r[4],i[5]=r[5],i[6]=r[6],i[7]=r[7],i[8]=r[8],i[9]=r[9],i[10]=r[10],i[11]=r[11],i[12]=r[12],i[13]=r[13],i[14]=r[14],i[15]=r[15],this}copyPosition(e){const i=this.elements,r=e.elements;return i[12]=r[12],i[13]=r[13],i[14]=r[14],this}setFromMatrix3(e){const i=e.elements;return this.set(i[0],i[3],i[6],0,i[1],i[4],i[7],0,i[2],i[5],i[8],0,0,0,0,1),this}extractBasis(e,i,r){return this.determinantAffine()===0?(e.set(1,0,0),i.set(0,1,0),r.set(0,0,1),this):(e.setFromMatrixColumn(this,0),i.setFromMatrixColumn(this,1),r.setFromMatrixColumn(this,2),this)}makeBasis(e,i,r){return this.set(e.x,i.x,r.x,0,e.y,i.y,r.y,0,e.z,i.z,r.z,0,0,0,0,1),this}extractRotation(e){if(e.determinantAffine()===0)return this.identity();const i=this.elements,r=e.elements,l=1/Er.setFromMatrixColumn(e,0).length(),u=1/Er.setFromMatrixColumn(e,1).length(),d=1/Er.setFromMatrixColumn(e,2).length();return i[0]=r[0]*l,i[1]=r[1]*l,i[2]=r[2]*l,i[3]=0,i[4]=r[4]*u,i[5]=r[5]*u,i[6]=r[6]*u,i[7]=0,i[8]=r[8]*d,i[9]=r[9]*d,i[10]=r[10]*d,i[11]=0,i[12]=0,i[13]=0,i[14]=0,i[15]=1,this}makeRotationFromEuler(e){const i=this.elements,r=e.x,l=e.y,u=e.z,d=Math.cos(r),h=Math.sin(r),m=Math.cos(l),p=Math.sin(l),v=Math.cos(u),x=Math.sin(u);if(e.order==="XYZ"){const g=d*v,E=d*x,T=h*v,D=h*x;i[0]=m*v,i[4]=-m*x,i[8]=p,i[1]=E+T*p,i[5]=g-D*p,i[9]=-h*m,i[2]=D-g*p,i[6]=T+E*p,i[10]=d*m}else if(e.order==="YXZ"){const g=m*v,E=m*x,T=p*v,D=p*x;i[0]=g+D*h,i[4]=T*h-E,i[8]=d*p,i[1]=d*x,i[5]=d*v,i[9]=-h,i[2]=E*h-T,i[6]=D+g*h,i[10]=d*m}else if(e.order==="ZXY"){const g=m*v,E=m*x,T=p*v,D=p*x;i[0]=g-D*h,i[4]=-d*x,i[8]=T+E*h,i[1]=E+T*h,i[5]=d*v,i[9]=D-g*h,i[2]=-d*p,i[6]=h,i[10]=d*m}else if(e.order==="ZYX"){const g=d*v,E=d*x,T=h*v,D=h*x;i[0]=m*v,i[4]=T*p-E,i[8]=g*p+D,i[1]=m*x,i[5]=D*p+g,i[9]=E*p-T,i[2]=-p,i[6]=h*m,i[10]=d*m}else if(e.order==="YZX"){const g=d*m,E=d*p,T=h*m,D=h*p;i[0]=m*v,i[4]=D-g*x,i[8]=T*x+E,i[1]=x,i[5]=d*v,i[9]=-h*v,i[2]=-p*v,i[6]=E*x+T,i[10]=g-D*x}else if(e.order==="XZY"){const g=d*m,E=d*p,T=h*m,D=h*p;i[0]=m*v,i[4]=-x,i[8]=p*v,i[1]=g*x+D,i[5]=d*v,i[9]=E*x-T,i[2]=T*x-E,i[6]=h*v,i[10]=D*x+g}return i[3]=0,i[7]=0,i[11]=0,i[12]=0,i[13]=0,i[14]=0,i[15]=1,this}makeRotationFromQuaternion(e){return this.compose(nE,e,iE)}lookAt(e,i,r){const l=this.elements;return ri.subVectors(e,i),ri.lengthSq()===0&&(ri.z=1),ri.normalize(),es.crossVectors(r,ri),es.lengthSq()===0&&(Math.abs(r.z)===1?ri.x+=1e-4:ri.z+=1e-4,ri.normalize(),es.crossVectors(r,ri)),es.normalize(),_c.crossVectors(ri,es),l[0]=es.x,l[4]=_c.x,l[8]=ri.x,l[1]=es.y,l[5]=_c.y,l[9]=ri.y,l[2]=es.z,l[6]=_c.z,l[10]=ri.z,this}multiply(e){return this.multiplyMatrices(this,e)}premultiply(e){return this.multiplyMatrices(e,this)}multiplyMatrices(e,i){const r=e.elements,l=i.elements,u=this.elements,d=r[0],h=r[4],m=r[8],p=r[12],v=r[1],x=r[5],g=r[9],E=r[13],T=r[2],D=r[6],y=r[10],S=r[14],z=r[3],F=r[7],C=r[11],O=r[15],U=l[0],B=l[4],b=l[8],L=l[12],W=l[1],V=l[5],Z=l[9],de=l[13],he=l[2],K=l[6],N=l[10],I=l[14],$=l[3],se=l[7],pe=l[11],xe=l[15];return u[0]=d*U+h*W+m*he+p*$,u[4]=d*B+h*V+m*K+p*se,u[8]=d*b+h*Z+m*N+p*pe,u[12]=d*L+h*de+m*I+p*xe,u[1]=v*U+x*W+g*he+E*$,u[5]=v*B+x*V+g*K+E*se,u[9]=v*b+x*Z+g*N+E*pe,u[13]=v*L+x*de+g*I+E*xe,u[2]=T*U+D*W+y*he+S*$,u[6]=T*B+D*V+y*K+S*se,u[10]=T*b+D*Z+y*N+S*pe,u[14]=T*L+D*de+y*I+S*xe,u[3]=z*U+F*W+C*he+O*$,u[7]=z*B+F*V+C*K+O*se,u[11]=z*b+F*Z+C*N+O*pe,u[15]=z*L+F*de+C*I+O*xe,this}multiplyScalar(e){const i=this.elements;return i[0]*=e,i[4]*=e,i[8]*=e,i[12]*=e,i[1]*=e,i[5]*=e,i[9]*=e,i[13]*=e,i[2]*=e,i[6]*=e,i[10]*=e,i[14]*=e,i[3]*=e,i[7]*=e,i[11]*=e,i[15]*=e,this}determinant(){const e=this.elements,i=e[0],r=e[4],l=e[8],u=e[12],d=e[1],h=e[5],m=e[9],p=e[13],v=e[2],x=e[6],g=e[10],E=e[14],T=e[3],D=e[7],y=e[11],S=e[15],z=m*E-p*g,F=h*E-p*x,C=h*g-m*x,O=d*E-p*v,U=d*g-m*v,B=d*x-h*v;return i*(D*z-y*F+S*C)-r*(T*z-y*O+S*U)+l*(T*F-D*O+S*B)-u*(T*C-D*U+y*B)}determinantAffine(){const e=this.elements,i=e[0],r=e[4],l=e[8],u=e[1],d=e[5],h=e[9],m=e[2],p=e[6],v=e[10];return i*(d*v-h*p)-r*(u*v-h*m)+l*(u*p-d*m)}transpose(){const e=this.elements;let i;return i=e[1],e[1]=e[4],e[4]=i,i=e[2],e[2]=e[8],e[8]=i,i=e[6],e[6]=e[9],e[9]=i,i=e[3],e[3]=e[12],e[12]=i,i=e[7],e[7]=e[13],e[13]=i,i=e[11],e[11]=e[14],e[14]=i,this}setPosition(e,i,r){const l=this.elements;return e.isVector3?(l[12]=e.x,l[13]=e.y,l[14]=e.z):(l[12]=e,l[13]=i,l[14]=r),this}invert(){const e=this.elements,i=e[0],r=e[1],l=e[2],u=e[3],d=e[4],h=e[5],m=e[6],p=e[7],v=e[8],x=e[9],g=e[10],E=e[11],T=e[12],D=e[13],y=e[14],S=e[15],z=i*h-r*d,F=i*m-l*d,C=i*p-u*d,O=r*m-l*h,U=r*p-u*h,B=l*p-u*m,b=v*D-x*T,L=v*y-g*T,W=v*S-E*T,V=x*y-g*D,Z=x*S-E*D,de=g*S-E*y,he=z*de-F*Z+C*V+O*W-U*L+B*b;if(he===0)return this.set(0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0);const K=1/he;return e[0]=(h*de-m*Z+p*V)*K,e[1]=(l*Z-r*de-u*V)*K,e[2]=(D*B-y*U+S*O)*K,e[3]=(g*U-x*B-E*O)*K,e[4]=(m*W-d*de-p*L)*K,e[5]=(i*de-l*W+u*L)*K,e[6]=(y*C-T*B-S*F)*K,e[7]=(v*B-g*C+E*F)*K,e[8]=(d*Z-h*W+p*b)*K,e[9]=(r*W-i*Z-u*b)*K,e[10]=(T*U-D*C+S*z)*K,e[11]=(x*C-v*U-E*z)*K,e[12]=(h*L-d*V-m*b)*K,e[13]=(i*V-r*L+l*b)*K,e[14]=(D*F-T*O-y*z)*K,e[15]=(v*O-x*F+g*z)*K,this}scale(e){const i=this.elements,r=e.x,l=e.y,u=e.z;return i[0]*=r,i[4]*=l,i[8]*=u,i[1]*=r,i[5]*=l,i[9]*=u,i[2]*=r,i[6]*=l,i[10]*=u,i[3]*=r,i[7]*=l,i[11]*=u,this}getMaxScaleOnAxis(){const e=this.elements,i=e[0]*e[0]+e[1]*e[1]+e[2]*e[2],r=e[4]*e[4]+e[5]*e[5]+e[6]*e[6],l=e[8]*e[8]+e[9]*e[9]+e[10]*e[10];return Math.sqrt(Math.max(i,r,l))}makeTranslation(e,i,r){return e.isVector3?this.set(1,0,0,e.x,0,1,0,e.y,0,0,1,e.z,0,0,0,1):this.set(1,0,0,e,0,1,0,i,0,0,1,r,0,0,0,1),this}makeRotationX(e){const i=Math.cos(e),r=Math.sin(e);return this.set(1,0,0,0,0,i,-r,0,0,r,i,0,0,0,0,1),this}makeRotationY(e){const i=Math.cos(e),r=Math.sin(e);return this.set(i,0,r,0,0,1,0,0,-r,0,i,0,0,0,0,1),this}makeRotationZ(e){const i=Math.cos(e),r=Math.sin(e);return this.set(i,-r,0,0,r,i,0,0,0,0,1,0,0,0,0,1),this}makeRotationAxis(e,i){const r=Math.cos(i),l=Math.sin(i),u=1-r,d=e.x,h=e.y,m=e.z,p=u*d,v=u*h;return this.set(p*d+r,p*h-l*m,p*m+l*h,0,p*h+l*m,v*h+r,v*m-l*d,0,p*m-l*h,v*m+l*d,u*m*m+r,0,0,0,0,1),this}makeScale(e,i,r){return this.set(e,0,0,0,0,i,0,0,0,0,r,0,0,0,0,1),this}makeShear(e,i,r,l,u,d){return this.set(1,r,u,0,e,1,d,0,i,l,1,0,0,0,0,1),this}compose(e,i,r){const l=this.elements,u=i._x,d=i._y,h=i._z,m=i._w,p=u+u,v=d+d,x=h+h,g=u*p,E=u*v,T=u*x,D=d*v,y=d*x,S=h*x,z=m*p,F=m*v,C=m*x,O=r.x,U=r.y,B=r.z;return l[0]=(1-(D+S))*O,l[1]=(E+C)*O,l[2]=(T-F)*O,l[3]=0,l[4]=(E-C)*U,l[5]=(1-(g+S))*U,l[6]=(y+z)*U,l[7]=0,l[8]=(T+F)*B,l[9]=(y-z)*B,l[10]=(1-(g+D))*B,l[11]=0,l[12]=e.x,l[13]=e.y,l[14]=e.z,l[15]=1,this}decompose(e,i,r){const l=this.elements;e.x=l[12],e.y=l[13],e.z=l[14];const u=this.determinantAffine();if(u===0)return r.set(1,1,1),i.identity(),this;let d=Er.set(l[0],l[1],l[2]).length();const h=Er.set(l[4],l[5],l[6]).length(),m=Er.set(l[8],l[9],l[10]).length();u<0&&(d=-d),Ai.copy(this);const p=1/d,v=1/h,x=1/m;return Ai.elements[0]*=p,Ai.elements[1]*=p,Ai.elements[2]*=p,Ai.elements[4]*=v,Ai.elements[5]*=v,Ai.elements[6]*=v,Ai.elements[8]*=x,Ai.elements[9]*=x,Ai.elements[10]*=x,i.setFromRotationMatrix(Ai),r.x=d,r.y=h,r.z=m,this}makePerspective(e,i,r,l,u,d,h=ki,m=!1){const p=this.elements,v=2*u/(i-e),x=2*u/(r-l),g=(i+e)/(i-e),E=(r+l)/(r-l);let T,D;if(m)T=u/(d-u),D=d*u/(d-u);else if(h===ki)T=-(d+u)/(d-u),D=-2*d*u/(d-u);else if(h===Jc)T=-d/(d-u),D=-d*u/(d-u);else throw new Error("THREE.Matrix4.makePerspective(): Invalid coordinate system: "+h);return p[0]=v,p[4]=0,p[8]=g,p[12]=0,p[1]=0,p[5]=x,p[9]=E,p[13]=0,p[2]=0,p[6]=0,p[10]=T,p[14]=D,p[3]=0,p[7]=0,p[11]=-1,p[15]=0,this}makeOrthographic(e,i,r,l,u,d,h=ki,m=!1){const p=this.elements,v=2/(i-e),x=2/(r-l),g=-(i+e)/(i-e),E=-(r+l)/(r-l);let T,D;if(m)T=1/(d-u),D=d/(d-u);else if(h===ki)T=-2/(d-u),D=-(d+u)/(d-u);else if(h===Jc)T=-1/(d-u),D=-u/(d-u);else throw new Error("THREE.Matrix4.makeOrthographic(): Invalid coordinate system: "+h);return p[0]=v,p[4]=0,p[8]=0,p[12]=g,p[1]=0,p[5]=x,p[9]=0,p[13]=E,p[2]=0,p[6]=0,p[10]=T,p[14]=D,p[3]=0,p[7]=0,p[11]=0,p[15]=1,this}equals(e){const i=this.elements,r=e.elements;for(let l=0;l<16;l++)if(i[l]!==r[l])return!1;return!0}fromArray(e,i=0){for(let r=0;r<16;r++)this.elements[r]=e[r+i];return this}toArray(e=[],i=0){const r=this.elements;return e[i]=r[0],e[i+1]=r[1],e[i+2]=r[2],e[i+3]=r[3],e[i+4]=r[4],e[i+5]=r[5],e[i+6]=r[6],e[i+7]=r[7],e[i+8]=r[8],e[i+9]=r[9],e[i+10]=r[10],e[i+11]=r[11],e[i+12]=r[12],e[i+13]=r[13],e[i+14]=r[14],e[i+15]=r[15],e}};jc.prototype.isMatrix4=!0;let fn=jc;const Er=new ae,Ai=new fn,nE=new ae(0,0,0),iE=new ae(1,1,1),es=new ae,_c=new ae,ri=new ae,r_=new fn,o_=new Wr;class Gs{constructor(e=0,i=0,r=0,l=Gs.DEFAULT_ORDER){this.isEuler=!0,this._x=e,this._y=i,this._z=r,this._order=l}get x(){return this._x}set x(e){this._x=e,this._onChangeCallback()}get y(){return this._y}set y(e){this._y=e,this._onChangeCallback()}get z(){return this._z}set z(e){this._z=e,this._onChangeCallback()}get order(){return this._order}set order(e){this._order=e,this._onChangeCallback()}set(e,i,r,l=this._order){return this._x=e,this._y=i,this._z=r,this._order=l,this._onChangeCallback(),this}clone(){return new this.constructor(this._x,this._y,this._z,this._order)}copy(e){return this._x=e._x,this._y=e._y,this._z=e._z,this._order=e._order,this._onChangeCallback(),this}setFromRotationMatrix(e,i=this._order,r=!0){const l=e.elements,u=l[0],d=l[4],h=l[8],m=l[1],p=l[5],v=l[9],x=l[2],g=l[6],E=l[10];switch(i){case"XYZ":this._y=Math.asin(vt(h,-1,1)),Math.abs(h)<.9999999?(this._x=Math.atan2(-v,E),this._z=Math.atan2(-d,u)):(this._x=Math.atan2(g,p),this._z=0);break;case"YXZ":this._x=Math.asin(-vt(v,-1,1)),Math.abs(v)<.9999999?(this._y=Math.atan2(h,E),this._z=Math.atan2(m,p)):(this._y=Math.atan2(-x,u),this._z=0);break;case"ZXY":this._x=Math.asin(vt(g,-1,1)),Math.abs(g)<.9999999?(this._y=Math.atan2(-x,E),this._z=Math.atan2(-d,p)):(this._y=0,this._z=Math.atan2(m,u));break;case"ZYX":this._y=Math.asin(-vt(x,-1,1)),Math.abs(x)<.9999999?(this._x=Math.atan2(g,E),this._z=Math.atan2(m,u)):(this._x=0,this._z=Math.atan2(-d,p));break;case"YZX":this._z=Math.asin(vt(m,-1,1)),Math.abs(m)<.9999999?(this._x=Math.atan2(-v,p),this._y=Math.atan2(-x,u)):(this._x=0,this._y=Math.atan2(h,E));break;case"XZY":this._z=Math.asin(-vt(d,-1,1)),Math.abs(d)<.9999999?(this._x=Math.atan2(g,p),this._y=Math.atan2(h,u)):(this._x=Math.atan2(-v,E),this._y=0);break;default:et("Euler: .setFromRotationMatrix() encountered an unknown order: "+i)}return this._order=i,r===!0&&this._onChangeCallback(),this}setFromQuaternion(e,i,r){return r_.makeRotationFromQuaternion(e),this.setFromRotationMatrix(r_,i,r)}setFromVector3(e,i=this._order){return this.set(e.x,e.y,e.z,i)}reorder(e){return o_.setFromEuler(this),this.setFromQuaternion(o_,e)}equals(e){return e._x===this._x&&e._y===this._y&&e._z===this._z&&e._order===this._order}fromArray(e){return this._x=e[0],this._y=e[1],this._z=e[2],e[3]!==void 0&&(this._order=e[3]),this._onChangeCallback(),this}toArray(e=[],i=0){return e[i]=this._x,e[i+1]=this._y,e[i+2]=this._z,e[i+3]=this._order,e}_onChange(e){return this._onChangeCallback=e,this}_onChangeCallback(){}*[Symbol.iterator](){yield this._x,yield this._y,yield this._z,yield this._order}}Gs.DEFAULT_ORDER="XYZ";class Sv{constructor(){this.mask=1}set(e){this.mask=(1<<e|0)>>>0}enable(e){this.mask|=1<<e|0}enableAll(){this.mask=-1}toggle(e){this.mask^=1<<e|0}disable(e){this.mask&=~(1<<e|0)}disableAll(){this.mask=0}test(e){return(this.mask&e.mask)!==0}isEnabled(e){return(this.mask&(1<<e|0))!==0}}let aE=0;const l_=new ae,br=new Wr,pa=new fn,vc=new ae,Go=new ae,sE=new ae,rE=new Wr,c_=new ae(1,0,0),u_=new ae(0,1,0),f_=new ae(0,0,1),d_={type:"added"},oE={type:"removed"},Tr={type:"childadded",child:null},xd={type:"childremoved",child:null};class Zn extends Vs{constructor(){super(),this.isObject3D=!0,Object.defineProperty(this,"id",{value:aE++}),this.uuid=Xr(),this.name="",this.type="Object3D",this.parent=null,this.children=[],this.up=Zn.DEFAULT_UP.clone();const e=new ae,i=new Gs,r=new Wr,l=new ae(1,1,1);function u(){r.setFromEuler(i,!1)}function d(){i.setFromQuaternion(r,void 0,!1)}i._onChange(u),r._onChange(d),Object.defineProperties(this,{position:{configurable:!0,enumerable:!0,value:e},rotation:{configurable:!0,enumerable:!0,value:i},quaternion:{configurable:!0,enumerable:!0,value:r},scale:{configurable:!0,enumerable:!0,value:l},modelViewMatrix:{value:new fn},normalMatrix:{value:new st}}),this.matrix=new fn,this.matrixWorld=new fn,this.matrixAutoUpdate=Zn.DEFAULT_MATRIX_AUTO_UPDATE,this.matrixWorldAutoUpdate=Zn.DEFAULT_MATRIX_WORLD_AUTO_UPDATE,this.matrixWorldNeedsUpdate=!1,this.layers=new Sv,this.visible=!0,this.castShadow=!1,this.receiveShadow=!1,this.frustumCulled=!0,this.renderOrder=0,this.animations=[],this.customDepthMaterial=void 0,this.customDistanceMaterial=void 0,this.static=!1,this.userData={},this.pivot=null}onBeforeShadow(){}onAfterShadow(){}onBeforeRender(){}onAfterRender(){}applyMatrix4(e){this.matrixAutoUpdate&&this.updateMatrix(),this.matrix.premultiply(e),this.matrix.decompose(this.position,this.quaternion,this.scale)}applyQuaternion(e){return this.quaternion.premultiply(e),this}setRotationFromAxisAngle(e,i){this.quaternion.setFromAxisAngle(e,i)}setRotationFromEuler(e){this.quaternion.setFromEuler(e,!0)}setRotationFromMatrix(e){this.quaternion.setFromRotationMatrix(e)}setRotationFromQuaternion(e){this.quaternion.copy(e)}rotateOnAxis(e,i){return br.setFromAxisAngle(e,i),this.quaternion.multiply(br),this}rotateOnWorldAxis(e,i){return br.setFromAxisAngle(e,i),this.quaternion.premultiply(br),this}rotateX(e){return this.rotateOnAxis(c_,e)}rotateY(e){return this.rotateOnAxis(u_,e)}rotateZ(e){return this.rotateOnAxis(f_,e)}translateOnAxis(e,i){return l_.copy(e).applyQuaternion(this.quaternion),this.position.add(l_.multiplyScalar(i)),this}translateX(e){return this.translateOnAxis(c_,e)}translateY(e){return this.translateOnAxis(u_,e)}translateZ(e){return this.translateOnAxis(f_,e)}localToWorld(e){return this.updateWorldMatrix(!0,!1),e.applyMatrix4(this.matrixWorld)}worldToLocal(e){return this.updateWorldMatrix(!0,!1),e.applyMatrix4(pa.copy(this.matrixWorld).invert())}lookAt(e,i,r){e.isVector3?vc.copy(e):vc.set(e,i,r);const l=this.parent;this.updateWorldMatrix(!0,!1),Go.setFromMatrixPosition(this.matrixWorld),this.isCamera||this.isLight?pa.lookAt(Go,vc,this.up):pa.lookAt(vc,Go,this.up),this.quaternion.setFromRotationMatrix(pa),l&&(pa.extractRotation(l.matrixWorld),br.setFromRotationMatrix(pa),this.quaternion.premultiply(br.invert()))}add(e){if(arguments.length>1){for(let i=0;i<arguments.length;i++)this.add(arguments[i]);return this}return e===this?(bt("Object3D.add: object can't be added as a child of itself.",e),this):(e&&e.isObject3D?(e.removeFromParent(),e.parent=this,this.children.push(e),e.dispatchEvent(d_),Tr.child=e,this.dispatchEvent(Tr),Tr.child=null):bt("Object3D.add: object not an instance of THREE.Object3D.",e),this)}remove(e){if(arguments.length>1){for(let r=0;r<arguments.length;r++)this.remove(arguments[r]);return this}const i=this.children.indexOf(e);return i!==-1&&(e.parent=null,this.children.splice(i,1),e.dispatchEvent(oE),xd.child=e,this.dispatchEvent(xd),xd.child=null),this}removeFromParent(){const e=this.parent;return e!==null&&e.remove(this),this}clear(){return this.remove(...this.children)}attach(e){return this.updateWorldMatrix(!0,!1),pa.copy(this.matrixWorld).invert(),e.parent!==null&&(e.parent.updateWorldMatrix(!0,!1),pa.multiply(e.parent.matrixWorld)),e.applyMatrix4(pa),e.removeFromParent(),e.parent=this,this.children.push(e),e.updateWorldMatrix(!1,!0),e.dispatchEvent(d_),Tr.child=e,this.dispatchEvent(Tr),Tr.child=null,this}getObjectById(e){return this.getObjectByProperty("id",e)}getObjectByName(e){return this.getObjectByProperty("name",e)}getObjectByProperty(e,i){if(this[e]===i)return this;for(let r=0,l=this.children.length;r<l;r++){const d=this.children[r].getObjectByProperty(e,i);if(d!==void 0)return d}}getObjectsByProperty(e,i,r=[]){this[e]===i&&r.push(this);const l=this.children;for(let u=0,d=l.length;u<d;u++)l[u].getObjectsByProperty(e,i,r);return r}getWorldPosition(e){return this.updateWorldMatrix(!0,!1),e.setFromMatrixPosition(this.matrixWorld)}getWorldQuaternion(e){return this.updateWorldMatrix(!0,!1),this.matrixWorld.decompose(Go,e,sE),e}getWorldScale(e){return this.updateWorldMatrix(!0,!1),this.matrixWorld.decompose(Go,rE,e),e}getWorldDirection(e){this.updateWorldMatrix(!0,!1);const i=this.matrixWorld.elements;return e.set(i[8],i[9],i[10]).normalize()}raycast(){}traverse(e){e(this);const i=this.children;for(let r=0,l=i.length;r<l;r++)i[r].traverse(e)}traverseVisible(e){if(this.visible===!1)return;e(this);const i=this.children;for(let r=0,l=i.length;r<l;r++)i[r].traverseVisible(e)}traverseAncestors(e){const i=this.parent;i!==null&&(e(i),i.traverseAncestors(e))}updateMatrix(){this.matrix.compose(this.position,this.quaternion,this.scale);const e=this.pivot;if(e!==null){const i=e.x,r=e.y,l=e.z,u=this.matrix.elements;u[12]+=i-u[0]*i-u[4]*r-u[8]*l,u[13]+=r-u[1]*i-u[5]*r-u[9]*l,u[14]+=l-u[2]*i-u[6]*r-u[10]*l}this.matrixWorldNeedsUpdate=!0}updateMatrixWorld(e){this.matrixAutoUpdate&&this.updateMatrix(),(this.matrixWorldNeedsUpdate||e)&&(this.matrixWorldAutoUpdate===!0&&(this.parent===null?this.matrixWorld.copy(this.matrix):this.matrixWorld.multiplyMatrices(this.parent.matrixWorld,this.matrix)),this.matrixWorldNeedsUpdate=!1,e=!0);const i=this.children;for(let r=0,l=i.length;r<l;r++)i[r].updateMatrixWorld(e)}updateWorldMatrix(e,i,r=!1){const l=this.parent;if(e===!0&&l!==null&&l.updateWorldMatrix(!0,!1),this.matrixAutoUpdate&&this.updateMatrix(),(this.matrixWorldNeedsUpdate||r)&&(this.matrixWorldAutoUpdate===!0&&(this.parent===null?this.matrixWorld.copy(this.matrix):this.matrixWorld.multiplyMatrices(this.parent.matrixWorld,this.matrix)),this.matrixWorldNeedsUpdate=!1,r=!0),i===!0){const u=this.children;for(let d=0,h=u.length;d<h;d++)u[d].updateWorldMatrix(!1,!0,r)}}toJSON(e){const i=e===void 0||typeof e=="string",r={};i&&(e={geometries:{},materials:{},textures:{},images:{},shapes:{},skeletons:{},animations:{},nodes:{}},r.metadata={version:4.7,type:"Object",generator:"Object3D.toJSON"});const l={};l.uuid=this.uuid,l.type=this.type,this.name!==""&&(l.name=this.name),this.castShadow===!0&&(l.castShadow=!0),this.receiveShadow===!0&&(l.receiveShadow=!0),this.visible===!1&&(l.visible=!1),this.frustumCulled===!1&&(l.frustumCulled=!1),this.renderOrder!==0&&(l.renderOrder=this.renderOrder),this.static!==!1&&(l.static=this.static),Object.keys(this.userData).length>0&&(l.userData=this.userData),l.layers=this.layers.mask,l.matrix=this.matrix.toArray(),l.up=this.up.toArray(),this.pivot!==null&&(l.pivot=this.pivot.toArray()),this.matrixAutoUpdate===!1&&(l.matrixAutoUpdate=!1),this.morphTargetDictionary!==void 0&&(l.morphTargetDictionary=Object.assign({},this.morphTargetDictionary)),this.morphTargetInfluences!==void 0&&(l.morphTargetInfluences=this.morphTargetInfluences.slice()),this.isInstancedMesh&&(l.type="InstancedMesh",l.count=this.count,l.instanceMatrix=this.instanceMatrix.toJSON(),this.instanceColor!==null&&(l.instanceColor=this.instanceColor.toJSON())),this.isBatchedMesh&&(l.type="BatchedMesh",l.perObjectFrustumCulled=this.perObjectFrustumCulled,l.sortObjects=this.sortObjects,l.drawRanges=this._drawRanges,l.reservedRanges=this._reservedRanges,l.geometryInfo=this._geometryInfo.map(h=>({...h,boundingBox:h.boundingBox?h.boundingBox.toJSON():void 0,boundingSphere:h.boundingSphere?h.boundingSphere.toJSON():void 0})),l.instanceInfo=this._instanceInfo.map(h=>({...h})),l.availableInstanceIds=this._availableInstanceIds.slice(),l.availableGeometryIds=this._availableGeometryIds.slice(),l.nextIndexStart=this._nextIndexStart,l.nextVertexStart=this._nextVertexStart,l.geometryCount=this._geometryCount,l.maxInstanceCount=this._maxInstanceCount,l.maxVertexCount=this._maxVertexCount,l.maxIndexCount=this._maxIndexCount,l.geometryInitialized=this._geometryInitialized,l.matricesTexture=this._matricesTexture.toJSON(e),l.indirectTexture=this._indirectTexture.toJSON(e),this._colorsTexture!==null&&(l.colorsTexture=this._colorsTexture.toJSON(e)),this.boundingSphere!==null&&(l.boundingSphere=this.boundingSphere.toJSON()),this.boundingBox!==null&&(l.boundingBox=this.boundingBox.toJSON()));function u(h,m){return h[m.uuid]===void 0&&(h[m.uuid]=m.toJSON(e)),m.uuid}if(this.isScene)this.background&&(this.background.isColor?l.background=this.background.toJSON():this.background.isTexture&&(l.background=this.background.toJSON(e).uuid)),this.environment&&this.environment.isTexture&&this.environment.isRenderTargetTexture!==!0&&(l.environment=this.environment.toJSON(e).uuid);else if(this.isMesh||this.isLine||this.isPoints){l.geometry=u(e.geometries,this.geometry);const h=this.geometry.parameters;if(h!==void 0&&h.shapes!==void 0){const m=h.shapes;if(Array.isArray(m))for(let p=0,v=m.length;p<v;p++){const x=m[p];u(e.shapes,x)}else u(e.shapes,m)}}if(this.isSkinnedMesh&&(l.bindMode=this.bindMode,l.bindMatrix=this.bindMatrix.toArray(),this.skeleton!==void 0&&(u(e.skeletons,this.skeleton),l.skeleton=this.skeleton.uuid)),this.material!==void 0)if(Array.isArray(this.material)){const h=[];for(let m=0,p=this.material.length;m<p;m++)h.push(u(e.materials,this.material[m]));l.material=h}else l.material=u(e.materials,this.material);if(this.children.length>0){l.children=[];for(let h=0;h<this.children.length;h++)l.children.push(this.children[h].toJSON(e).object)}if(this.animations.length>0){l.animations=[];for(let h=0;h<this.animations.length;h++){const m=this.animations[h];l.animations.push(u(e.animations,m))}}if(i){const h=d(e.geometries),m=d(e.materials),p=d(e.textures),v=d(e.images),x=d(e.shapes),g=d(e.skeletons),E=d(e.animations),T=d(e.nodes);h.length>0&&(r.geometries=h),m.length>0&&(r.materials=m),p.length>0&&(r.textures=p),v.length>0&&(r.images=v),x.length>0&&(r.shapes=x),g.length>0&&(r.skeletons=g),E.length>0&&(r.animations=E),T.length>0&&(r.nodes=T)}return r.object=l,r;function d(h){const m=[];for(const p in h){const v=h[p];delete v.metadata,m.push(v)}return m}}clone(e){return new this.constructor().copy(this,e)}copy(e,i=!0){if(this.name=e.name,this.up.copy(e.up),this.position.copy(e.position),this.rotation.order=e.rotation.order,this.quaternion.copy(e.quaternion),this.scale.copy(e.scale),this.pivot=e.pivot!==null?e.pivot.clone():null,this.matrix.copy(e.matrix),this.matrixWorld.copy(e.matrixWorld),this.matrixAutoUpdate=e.matrixAutoUpdate,this.matrixWorldAutoUpdate=e.matrixWorldAutoUpdate,this.matrixWorldNeedsUpdate=e.matrixWorldNeedsUpdate,this.layers.mask=e.layers.mask,this.visible=e.visible,this.castShadow=e.castShadow,this.receiveShadow=e.receiveShadow,this.frustumCulled=e.frustumCulled,this.renderOrder=e.renderOrder,this.static=e.static,this.animations=e.animations.slice(),this.userData=JSON.parse(JSON.stringify(e.userData)),i===!0)for(let r=0;r<e.children.length;r++){const l=e.children[r];this.add(l.clone())}return this}}Zn.DEFAULT_UP=new ae(0,1,0);Zn.DEFAULT_MATRIX_AUTO_UPDATE=!0;Zn.DEFAULT_MATRIX_WORLD_AUTO_UPDATE=!0;class xc extends Zn{constructor(){super(),this.isGroup=!0,this.type="Group"}}const lE={type:"move"};class Sd{constructor(){this._targetRay=null,this._grip=null,this._hand=null}getHandSpace(){return this._hand===null&&(this._hand=new xc,this._hand.matrixAutoUpdate=!1,this._hand.visible=!1,this._hand.joints={},this._hand.inputState={pinching:!1}),this._hand}getTargetRaySpace(){return this._targetRay===null&&(this._targetRay=new xc,this._targetRay.matrixAutoUpdate=!1,this._targetRay.visible=!1,this._targetRay.hasLinearVelocity=!1,this._targetRay.linearVelocity=new ae,this._targetRay.hasAngularVelocity=!1,this._targetRay.angularVelocity=new ae),this._targetRay}getGripSpace(){return this._grip===null&&(this._grip=new xc,this._grip.matrixAutoUpdate=!1,this._grip.visible=!1,this._grip.hasLinearVelocity=!1,this._grip.linearVelocity=new ae,this._grip.hasAngularVelocity=!1,this._grip.angularVelocity=new ae,this._grip.eventsEnabled=!1),this._grip}dispatchEvent(e){return this._targetRay!==null&&this._targetRay.dispatchEvent(e),this._grip!==null&&this._grip.dispatchEvent(e),this._hand!==null&&this._hand.dispatchEvent(e),this}connect(e){if(e&&e.hand){const i=this._hand;if(i)for(const r of e.hand.values())this._getHandJoint(i,r)}return this.dispatchEvent({type:"connected",data:e}),this}disconnect(e){return this.dispatchEvent({type:"disconnected",data:e}),this._targetRay!==null&&(this._targetRay.visible=!1),this._grip!==null&&(this._grip.visible=!1),this._hand!==null&&(this._hand.visible=!1),this}update(e,i,r){let l=null,u=null,d=null;const h=this._targetRay,m=this._grip,p=this._hand;if(e&&i.session.visibilityState!=="visible-blurred"){if(p&&e.hand){d=!0;for(const D of e.hand.values()){const y=i.getJointPose(D,r),S=this._getHandJoint(p,D);y!==null&&(S.matrix.fromArray(y.transform.matrix),S.matrix.decompose(S.position,S.rotation,S.scale),S.matrixWorldNeedsUpdate=!0,S.jointRadius=y.radius),S.visible=y!==null}const v=p.joints["index-finger-tip"],x=p.joints["thumb-tip"],g=v.position.distanceTo(x.position),E=.02,T=.005;p.inputState.pinching&&g>E+T?(p.inputState.pinching=!1,this.dispatchEvent({type:"pinchend",handedness:e.handedness,target:this})):!p.inputState.pinching&&g<=E-T&&(p.inputState.pinching=!0,this.dispatchEvent({type:"pinchstart",handedness:e.handedness,target:this}))}else m!==null&&e.gripSpace&&(u=i.getPose(e.gripSpace,r),u!==null&&(m.matrix.fromArray(u.transform.matrix),m.matrix.decompose(m.position,m.rotation,m.scale),m.matrixWorldNeedsUpdate=!0,u.linearVelocity?(m.hasLinearVelocity=!0,m.linearVelocity.copy(u.linearVelocity)):m.hasLinearVelocity=!1,u.angularVelocity?(m.hasAngularVelocity=!0,m.angularVelocity.copy(u.angularVelocity)):m.hasAngularVelocity=!1,m.eventsEnabled&&m.dispatchEvent({type:"gripUpdated",data:e,target:this})));h!==null&&(l=i.getPose(e.targetRaySpace,r),l===null&&u!==null&&(l=u),l!==null&&(h.matrix.fromArray(l.transform.matrix),h.matrix.decompose(h.position,h.rotation,h.scale),h.matrixWorldNeedsUpdate=!0,l.linearVelocity?(h.hasLinearVelocity=!0,h.linearVelocity.copy(l.linearVelocity)):h.hasLinearVelocity=!1,l.angularVelocity?(h.hasAngularVelocity=!0,h.angularVelocity.copy(l.angularVelocity)):h.hasAngularVelocity=!1,this.dispatchEvent(lE)))}return h!==null&&(h.visible=l!==null),m!==null&&(m.visible=u!==null),p!==null&&(p.visible=d!==null),this}_getHandJoint(e,i){if(e.joints[i.jointName]===void 0){const r=new xc;r.matrixAutoUpdate=!1,r.visible=!1,e.joints[i.jointName]=r,e.add(r)}return e.joints[i.jointName]}}const Mv={aliceblue:15792383,antiquewhite:16444375,aqua:65535,aquamarine:8388564,azure:15794175,beige:16119260,bisque:16770244,black:0,blanchedalmond:16772045,blue:255,blueviolet:9055202,brown:10824234,burlywood:14596231,cadetblue:6266528,chartreuse:8388352,chocolate:13789470,coral:16744272,cornflowerblue:6591981,cornsilk:16775388,crimson:14423100,cyan:65535,darkblue:139,darkcyan:35723,darkgoldenrod:12092939,darkgray:11119017,darkgreen:25600,darkgrey:11119017,darkkhaki:12433259,darkmagenta:9109643,darkolivegreen:5597999,darkorange:16747520,darkorchid:10040012,darkred:9109504,darksalmon:15308410,darkseagreen:9419919,darkslateblue:4734347,darkslategray:3100495,darkslategrey:3100495,darkturquoise:52945,darkviolet:9699539,deeppink:16716947,deepskyblue:49151,dimgray:6908265,dimgrey:6908265,dodgerblue:2003199,firebrick:11674146,floralwhite:16775920,forestgreen:2263842,fuchsia:16711935,gainsboro:14474460,ghostwhite:16316671,gold:16766720,goldenrod:14329120,gray:8421504,green:32768,greenyellow:11403055,grey:8421504,honeydew:15794160,hotpink:16738740,indianred:13458524,indigo:4915330,ivory:16777200,khaki:15787660,lavender:15132410,lavenderblush:16773365,lawngreen:8190976,lemonchiffon:16775885,lightblue:11393254,lightcoral:15761536,lightcyan:14745599,lightgoldenrodyellow:16448210,lightgray:13882323,lightgreen:9498256,lightgrey:13882323,lightpink:16758465,lightsalmon:16752762,lightseagreen:2142890,lightskyblue:8900346,lightslategray:7833753,lightslategrey:7833753,lightsteelblue:11584734,lightyellow:16777184,lime:65280,limegreen:3329330,linen:16445670,magenta:16711935,maroon:8388608,mediumaquamarine:6737322,mediumblue:205,mediumorchid:12211667,mediumpurple:9662683,mediumseagreen:3978097,mediumslateblue:8087790,mediumspringgreen:64154,mediumturquoise:4772300,mediumvioletred:13047173,midnightblue:1644912,mintcream:16121850,mistyrose:16770273,moccasin:16770229,navajowhite:16768685,navy:128,oldlace:16643558,olive:8421376,olivedrab:7048739,orange:16753920,orangered:16729344,orchid:14315734,palegoldenrod:15657130,palegreen:10025880,paleturquoise:11529966,palevioletred:14381203,papayawhip:16773077,peachpuff:16767673,peru:13468991,pink:16761035,plum:14524637,powderblue:11591910,purple:8388736,rebeccapurple:6697881,red:16711680,rosybrown:12357519,royalblue:4286945,saddlebrown:9127187,salmon:16416882,sandybrown:16032864,seagreen:3050327,seashell:16774638,sienna:10506797,silver:12632256,skyblue:8900331,slateblue:6970061,slategray:7372944,slategrey:7372944,snow:16775930,springgreen:65407,steelblue:4620980,tan:13808780,teal:32896,thistle:14204888,tomato:16737095,turquoise:4251856,violet:15631086,wheat:16113331,white:16777215,whitesmoke:16119285,yellow:16776960,yellowgreen:10145074},ts={h:0,s:0,l:0},Sc={h:0,s:0,l:0};function Md(s,e,i){return i<0&&(i+=1),i>1&&(i-=1),i<1/6?s+(e-s)*6*i:i<1/2?e:i<2/3?s+(e-s)*6*(2/3-i):s}class Et{constructor(e,i,r){return this.isColor=!0,this.r=1,this.g=1,this.b=1,this.set(e,i,r)}set(e,i,r){if(i===void 0&&r===void 0){const l=e;l&&l.isColor?this.copy(l):typeof l=="number"?this.setHex(l):typeof l=="string"&&this.setStyle(l)}else this.setRGB(e,i,r);return this}setScalar(e){return this.r=e,this.g=e,this.b=e,this}setHex(e,i=_i){return e=Math.floor(e),this.r=(e>>16&255)/255,this.g=(e>>8&255)/255,this.b=(e&255)/255,yt.colorSpaceToWorking(this,i),this}setRGB(e,i,r,l=yt.workingColorSpace){return this.r=e,this.g=i,this.b=r,yt.colorSpaceToWorking(this,l),this}setHSL(e,i,r,l=yt.workingColorSpace){if(e=Yh(e,1),i=vt(i,0,1),r=vt(r,0,1),i===0)this.r=this.g=this.b=r;else{const u=r<=.5?r*(1+i):r+i-r*i,d=2*r-u;this.r=Md(d,u,e+1/3),this.g=Md(d,u,e),this.b=Md(d,u,e-1/3)}return yt.colorSpaceToWorking(this,l),this}setStyle(e,i=_i){function r(u){u!==void 0&&parseFloat(u)<1&&et("Color: Alpha component of "+e+" will be ignored.")}let l;if(l=/^(\w+)\(([^\)]*)\)/.exec(e)){let u;const d=l[1],h=l[2];switch(d){case"rgb":case"rgba":if(u=/^\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*(\d*\.?\d+)\s*)?$/.exec(h))return r(u[4]),this.setRGB(Math.min(255,parseInt(u[1],10))/255,Math.min(255,parseInt(u[2],10))/255,Math.min(255,parseInt(u[3],10))/255,i);if(u=/^\s*(\d+)\%\s*,\s*(\d+)\%\s*,\s*(\d+)\%\s*(?:,\s*(\d*\.?\d+)\s*)?$/.exec(h))return r(u[4]),this.setRGB(Math.min(100,parseInt(u[1],10))/100,Math.min(100,parseInt(u[2],10))/100,Math.min(100,parseInt(u[3],10))/100,i);break;case"hsl":case"hsla":if(u=/^\s*(\d*\.?\d+)\s*,\s*(\d*\.?\d+)\%\s*,\s*(\d*\.?\d+)\%\s*(?:,\s*(\d*\.?\d+)\s*)?$/.exec(h))return r(u[4]),this.setHSL(parseFloat(u[1])/360,parseFloat(u[2])/100,parseFloat(u[3])/100,i);break;default:et("Color: Unknown color model "+e)}}else if(l=/^\#([A-Fa-f\d]+)$/.exec(e)){const u=l[1],d=u.length;if(d===3)return this.setRGB(parseInt(u.charAt(0),16)/15,parseInt(u.charAt(1),16)/15,parseInt(u.charAt(2),16)/15,i);if(d===6)return this.setHex(parseInt(u,16),i);et("Color: Invalid hex color "+e)}else if(e&&e.length>0)return this.setColorName(e,i);return this}setColorName(e,i=_i){const r=Mv[e.toLowerCase()];return r!==void 0?this.setHex(r,i):et("Color: Unknown color "+e),this}clone(){return new this.constructor(this.r,this.g,this.b)}copy(e){return this.r=e.r,this.g=e.g,this.b=e.b,this}copySRGBToLinear(e){return this.r=ya(e.r),this.g=ya(e.g),this.b=ya(e.b),this}copyLinearToSRGB(e){return this.r=Ir(e.r),this.g=Ir(e.g),this.b=Ir(e.b),this}convertSRGBToLinear(){return this.copySRGBToLinear(this),this}convertLinearToSRGB(){return this.copyLinearToSRGB(this),this}getHex(e=_i){return yt.workingToColorSpace(Nn.copy(this),e),Math.round(vt(Nn.r*255,0,255))*65536+Math.round(vt(Nn.g*255,0,255))*256+Math.round(vt(Nn.b*255,0,255))}getHexString(e=_i){return("000000"+this.getHex(e).toString(16)).slice(-6)}getHSL(e,i=yt.workingColorSpace){yt.workingToColorSpace(Nn.copy(this),i);const r=Nn.r,l=Nn.g,u=Nn.b,d=Math.max(r,l,u),h=Math.min(r,l,u);let m,p;const v=(h+d)/2;if(h===d)m=0,p=0;else{const x=d-h;switch(p=v<=.5?x/(d+h):x/(2-d-h),d){case r:m=(l-u)/x+(l<u?6:0);break;case l:m=(u-r)/x+2;break;case u:m=(r-l)/x+4;break}m/=6}return e.h=m,e.s=p,e.l=v,e}getRGB(e,i=yt.workingColorSpace){return yt.workingToColorSpace(Nn.copy(this),i),e.r=Nn.r,e.g=Nn.g,e.b=Nn.b,e}getStyle(e=_i){yt.workingToColorSpace(Nn.copy(this),e);const i=Nn.r,r=Nn.g,l=Nn.b;return e!==_i?`color(${e} ${i.toFixed(3)} ${r.toFixed(3)} ${l.toFixed(3)})`:`rgb(${Math.round(i*255)},${Math.round(r*255)},${Math.round(l*255)})`}offsetHSL(e,i,r){return this.getHSL(ts),this.setHSL(ts.h+e,ts.s+i,ts.l+r)}add(e){return this.r+=e.r,this.g+=e.g,this.b+=e.b,this}addColors(e,i){return this.r=e.r+i.r,this.g=e.g+i.g,this.b=e.b+i.b,this}addScalar(e){return this.r+=e,this.g+=e,this.b+=e,this}sub(e){return this.r=Math.max(0,this.r-e.r),this.g=Math.max(0,this.g-e.g),this.b=Math.max(0,this.b-e.b),this}multiply(e){return this.r*=e.r,this.g*=e.g,this.b*=e.b,this}multiplyScalar(e){return this.r*=e,this.g*=e,this.b*=e,this}lerp(e,i){return this.r+=(e.r-this.r)*i,this.g+=(e.g-this.g)*i,this.b+=(e.b-this.b)*i,this}lerpColors(e,i,r){return this.r=e.r+(i.r-e.r)*r,this.g=e.g+(i.g-e.g)*r,this.b=e.b+(i.b-e.b)*r,this}lerpHSL(e,i){this.getHSL(ts),e.getHSL(Sc);const r=Qo(ts.h,Sc.h,i),l=Qo(ts.s,Sc.s,i),u=Qo(ts.l,Sc.l,i);return this.setHSL(r,l,u),this}setFromVector3(e){return this.r=e.x,this.g=e.y,this.b=e.z,this}applyMatrix3(e){const i=this.r,r=this.g,l=this.b,u=e.elements;return this.r=u[0]*i+u[3]*r+u[6]*l,this.g=u[1]*i+u[4]*r+u[7]*l,this.b=u[2]*i+u[5]*r+u[8]*l,this}equals(e){return e.r===this.r&&e.g===this.g&&e.b===this.b}fromArray(e,i=0){return this.r=e[i],this.g=e[i+1],this.b=e[i+2],this}toArray(e=[],i=0){return e[i]=this.r,e[i+1]=this.g,e[i+2]=this.b,e}fromBufferAttribute(e,i){return this.r=e.getX(i),this.g=e.getY(i),this.b=e.getZ(i),this}toJSON(){return this.getHex()}*[Symbol.iterator](){yield this.r,yield this.g,yield this.b}}const Nn=new Et;Et.NAMES=Mv;class Kh{constructor(e,i=25e-5){this.isFogExp2=!0,this.name="",this.color=new Et(e),this.density=i}clone(){return new Kh(this.color,this.density)}toJSON(){return{type:"FogExp2",name:this.name,color:this.color.getHex(),density:this.density}}}class cE extends Zn{constructor(){super(),this.isScene=!0,this.type="Scene",this.background=null,this.environment=null,this.fog=null,this.backgroundBlurriness=0,this.backgroundIntensity=1,this.backgroundRotation=new Gs,this.environmentIntensity=1,this.environmentRotation=new Gs,this.overrideMaterial=null,typeof __THREE_DEVTOOLS__<"u"&&__THREE_DEVTOOLS__.dispatchEvent(new CustomEvent("observe",{detail:this}))}copy(e,i){return super.copy(e,i),e.background!==null&&(this.background=e.background.clone()),e.environment!==null&&(this.environment=e.environment.clone()),e.fog!==null&&(this.fog=e.fog.clone()),this.backgroundBlurriness=e.backgroundBlurriness,this.backgroundIntensity=e.backgroundIntensity,this.backgroundRotation.copy(e.backgroundRotation),this.environmentIntensity=e.environmentIntensity,this.environmentRotation.copy(e.environmentRotation),e.overrideMaterial!==null&&(this.overrideMaterial=e.overrideMaterial.clone()),this.matrixAutoUpdate=e.matrixAutoUpdate,this}toJSON(e){const i=super.toJSON(e);return this.fog!==null&&(i.object.fog=this.fog.toJSON()),this.backgroundBlurriness>0&&(i.object.backgroundBlurriness=this.backgroundBlurriness),this.backgroundIntensity!==1&&(i.object.backgroundIntensity=this.backgroundIntensity),i.object.backgroundRotation=this.backgroundRotation.toArray(),this.environmentIntensity!==1&&(i.object.environmentIntensity=this.environmentIntensity),i.object.environmentRotation=this.environmentRotation.toArray(),i}}const Ri=new ae,ma=new ae,yd=new ae,ga=new ae,Ar=new ae,Rr=new ae,h_=new ae,Ed=new ae,bd=new ae,Td=new ae,Ad=new on,Rd=new on,Cd=new on;class wi{constructor(e=new ae,i=new ae,r=new ae){this.a=e,this.b=i,this.c=r}static getNormal(e,i,r,l){l.subVectors(r,i),Ri.subVectors(e,i),l.cross(Ri);const u=l.lengthSq();return u>0?l.multiplyScalar(1/Math.sqrt(u)):l.set(0,0,0)}static getBarycoord(e,i,r,l,u){Ri.subVectors(l,i),ma.subVectors(r,i),yd.subVectors(e,i);const d=Ri.dot(Ri),h=Ri.dot(ma),m=Ri.dot(yd),p=ma.dot(ma),v=ma.dot(yd),x=d*p-h*h;if(x===0)return u.set(0,0,0),null;const g=1/x,E=(p*m-h*v)*g,T=(d*v-h*m)*g;return u.set(1-E-T,T,E)}static containsPoint(e,i,r,l){return this.getBarycoord(e,i,r,l,ga)===null?!1:ga.x>=0&&ga.y>=0&&ga.x+ga.y<=1}static getInterpolation(e,i,r,l,u,d,h,m){return this.getBarycoord(e,i,r,l,ga)===null?(m.x=0,m.y=0,"z"in m&&(m.z=0),"w"in m&&(m.w=0),null):(m.setScalar(0),m.addScaledVector(u,ga.x),m.addScaledVector(d,ga.y),m.addScaledVector(h,ga.z),m)}static getInterpolatedAttribute(e,i,r,l,u,d){return Ad.setScalar(0),Rd.setScalar(0),Cd.setScalar(0),Ad.fromBufferAttribute(e,i),Rd.fromBufferAttribute(e,r),Cd.fromBufferAttribute(e,l),d.setScalar(0),d.addScaledVector(Ad,u.x),d.addScaledVector(Rd,u.y),d.addScaledVector(Cd,u.z),d}static isFrontFacing(e,i,r,l){return Ri.subVectors(r,i),ma.subVectors(e,i),Ri.cross(ma).dot(l)<0}set(e,i,r){return this.a.copy(e),this.b.copy(i),this.c.copy(r),this}setFromPointsAndIndices(e,i,r,l){return this.a.copy(e[i]),this.b.copy(e[r]),this.c.copy(e[l]),this}setFromAttributeAndIndices(e,i,r,l){return this.a.fromBufferAttribute(e,i),this.b.fromBufferAttribute(e,r),this.c.fromBufferAttribute(e,l),this}clone(){return new this.constructor().copy(this)}copy(e){return this.a.copy(e.a),this.b.copy(e.b),this.c.copy(e.c),this}getArea(){return Ri.subVectors(this.c,this.b),ma.subVectors(this.a,this.b),Ri.cross(ma).length()*.5}getMidpoint(e){return e.addVectors(this.a,this.b).add(this.c).multiplyScalar(1/3)}getNormal(e){return wi.getNormal(this.a,this.b,this.c,e)}getPlane(e){return e.setFromCoplanarPoints(this.a,this.b,this.c)}getBarycoord(e,i){return wi.getBarycoord(e,this.a,this.b,this.c,i)}getInterpolation(e,i,r,l,u){return wi.getInterpolation(e,this.a,this.b,this.c,i,r,l,u)}containsPoint(e){return wi.containsPoint(e,this.a,this.b,this.c)}isFrontFacing(e){return wi.isFrontFacing(this.a,this.b,this.c,e)}intersectsBox(e){return e.intersectsTriangle(this)}closestPointToPoint(e,i){const r=this.a,l=this.b,u=this.c;let d,h;Ar.subVectors(l,r),Rr.subVectors(u,r),Ed.subVectors(e,r);const m=Ar.dot(Ed),p=Rr.dot(Ed);if(m<=0&&p<=0)return i.copy(r);bd.subVectors(e,l);const v=Ar.dot(bd),x=Rr.dot(bd);if(v>=0&&x<=v)return i.copy(l);const g=m*x-v*p;if(g<=0&&m>=0&&v<=0)return d=m/(m-v),i.copy(r).addScaledVector(Ar,d);Td.subVectors(e,u);const E=Ar.dot(Td),T=Rr.dot(Td);if(T>=0&&E<=T)return i.copy(u);const D=E*p-m*T;if(D<=0&&p>=0&&T<=0)return h=p/(p-T),i.copy(r).addScaledVector(Rr,h);const y=v*T-E*x;if(y<=0&&x-v>=0&&E-T>=0)return h_.subVectors(u,l),h=(x-v)/(x-v+(E-T)),i.copy(l).addScaledVector(h_,h);const S=1/(y+D+g);return d=D*S,h=g*S,i.copy(r).addScaledVector(Ar,d).addScaledVector(Rr,h)}equals(e){return e.a.equals(this.a)&&e.b.equals(this.b)&&e.c.equals(this.c)}}class el{constructor(e=new ae(1/0,1/0,1/0),i=new ae(-1/0,-1/0,-1/0)){this.isBox3=!0,this.min=e,this.max=i}set(e,i){return this.min.copy(e),this.max.copy(i),this}setFromArray(e){this.makeEmpty();for(let i=0,r=e.length;i<r;i+=3)this.expandByPoint(Ci.fromArray(e,i));return this}setFromBufferAttribute(e){this.makeEmpty();for(let i=0,r=e.count;i<r;i++)this.expandByPoint(Ci.fromBufferAttribute(e,i));return this}setFromPoints(e){this.makeEmpty();for(let i=0,r=e.length;i<r;i++)this.expandByPoint(e[i]);return this}setFromCenterAndSize(e,i){const r=Ci.copy(i).multiplyScalar(.5);return this.min.copy(e).sub(r),this.max.copy(e).add(r),this}setFromObject(e,i=!1){return this.makeEmpty(),this.expandByObject(e,i)}clone(){return new this.constructor().copy(this)}copy(e){return this.min.copy(e.min),this.max.copy(e.max),this}makeEmpty(){return this.min.x=this.min.y=this.min.z=1/0,this.max.x=this.max.y=this.max.z=-1/0,this}isEmpty(){return this.max.x<this.min.x||this.max.y<this.min.y||this.max.z<this.min.z}getCenter(e){return this.isEmpty()?e.set(0,0,0):e.addVectors(this.min,this.max).multiplyScalar(.5)}getSize(e){return this.isEmpty()?e.set(0,0,0):e.subVectors(this.max,this.min)}expandByPoint(e){return this.min.min(e),this.max.max(e),this}expandByVector(e){return this.min.sub(e),this.max.add(e),this}expandByScalar(e){return this.min.addScalar(-e),this.max.addScalar(e),this}expandByObject(e,i=!1){e.updateWorldMatrix(!1,!1);const r=e.geometry;if(r!==void 0){const u=r.getAttribute("position");if(i===!0&&u!==void 0&&e.isInstancedMesh!==!0)for(let d=0,h=u.count;d<h;d++)e.isMesh===!0?e.getVertexPosition(d,Ci):Ci.fromBufferAttribute(u,d),Ci.applyMatrix4(e.matrixWorld),this.expandByPoint(Ci);else e.boundingBox!==void 0?(e.boundingBox===null&&e.computeBoundingBox(),Mc.copy(e.boundingBox)):(r.boundingBox===null&&r.computeBoundingBox(),Mc.copy(r.boundingBox)),Mc.applyMatrix4(e.matrixWorld),this.union(Mc)}const l=e.children;for(let u=0,d=l.length;u<d;u++)this.expandByObject(l[u],i);return this}containsPoint(e){return e.x>=this.min.x&&e.x<=this.max.x&&e.y>=this.min.y&&e.y<=this.max.y&&e.z>=this.min.z&&e.z<=this.max.z}containsBox(e){return this.min.x<=e.min.x&&e.max.x<=this.max.x&&this.min.y<=e.min.y&&e.max.y<=this.max.y&&this.min.z<=e.min.z&&e.max.z<=this.max.z}getParameter(e,i){return i.set((e.x-this.min.x)/(this.max.x-this.min.x),(e.y-this.min.y)/(this.max.y-this.min.y),(e.z-this.min.z)/(this.max.z-this.min.z))}intersectsBox(e){return e.max.x>=this.min.x&&e.min.x<=this.max.x&&e.max.y>=this.min.y&&e.min.y<=this.max.y&&e.max.z>=this.min.z&&e.min.z<=this.max.z}intersectsSphere(e){return this.clampPoint(e.center,Ci),Ci.distanceToSquared(e.center)<=e.radius*e.radius}intersectsPlane(e){let i,r;return e.normal.x>0?(i=e.normal.x*this.min.x,r=e.normal.x*this.max.x):(i=e.normal.x*this.max.x,r=e.normal.x*this.min.x),e.normal.y>0?(i+=e.normal.y*this.min.y,r+=e.normal.y*this.max.y):(i+=e.normal.y*this.max.y,r+=e.normal.y*this.min.y),e.normal.z>0?(i+=e.normal.z*this.min.z,r+=e.normal.z*this.max.z):(i+=e.normal.z*this.max.z,r+=e.normal.z*this.min.z),i<=-e.constant&&r>=-e.constant}intersectsTriangle(e){if(this.isEmpty())return!1;this.getCenter(Vo),yc.subVectors(this.max,Vo),Cr.subVectors(e.a,Vo),wr.subVectors(e.b,Vo),Dr.subVectors(e.c,Vo),ns.subVectors(wr,Cr),is.subVectors(Dr,wr),Ds.subVectors(Cr,Dr);let i=[0,-ns.z,ns.y,0,-is.z,is.y,0,-Ds.z,Ds.y,ns.z,0,-ns.x,is.z,0,-is.x,Ds.z,0,-Ds.x,-ns.y,ns.x,0,-is.y,is.x,0,-Ds.y,Ds.x,0];return!wd(i,Cr,wr,Dr,yc)||(i=[1,0,0,0,1,0,0,0,1],!wd(i,Cr,wr,Dr,yc))?!1:(Ec.crossVectors(ns,is),i=[Ec.x,Ec.y,Ec.z],wd(i,Cr,wr,Dr,yc))}clampPoint(e,i){return i.copy(e).clamp(this.min,this.max)}distanceToPoint(e){return this.clampPoint(e,Ci).distanceTo(e)}getBoundingSphere(e){return this.isEmpty()?e.makeEmpty():(this.getCenter(e.center),e.radius=this.getSize(Ci).length()*.5),e}intersect(e){return this.min.max(e.min),this.max.min(e.max),this.isEmpty()&&this.makeEmpty(),this}union(e){return this.min.min(e.min),this.max.max(e.max),this}applyMatrix4(e){return this.isEmpty()?this:(_a[0].set(this.min.x,this.min.y,this.min.z).applyMatrix4(e),_a[1].set(this.min.x,this.min.y,this.max.z).applyMatrix4(e),_a[2].set(this.min.x,this.max.y,this.min.z).applyMatrix4(e),_a[3].set(this.min.x,this.max.y,this.max.z).applyMatrix4(e),_a[4].set(this.max.x,this.min.y,this.min.z).applyMatrix4(e),_a[5].set(this.max.x,this.min.y,this.max.z).applyMatrix4(e),_a[6].set(this.max.x,this.max.y,this.min.z).applyMatrix4(e),_a[7].set(this.max.x,this.max.y,this.max.z).applyMatrix4(e),this.setFromPoints(_a),this)}translate(e){return this.min.add(e),this.max.add(e),this}equals(e){return e.min.equals(this.min)&&e.max.equals(this.max)}toJSON(){return{min:this.min.toArray(),max:this.max.toArray()}}fromJSON(e){return this.min.fromArray(e.min),this.max.fromArray(e.max),this}}const _a=[new ae,new ae,new ae,new ae,new ae,new ae,new ae,new ae],Ci=new ae,Mc=new el,Cr=new ae,wr=new ae,Dr=new ae,ns=new ae,is=new ae,Ds=new ae,Vo=new ae,yc=new ae,Ec=new ae,Us=new ae;function wd(s,e,i,r,l){for(let u=0,d=s.length-3;u<=d;u+=3){Us.fromArray(s,u);const h=l.x*Math.abs(Us.x)+l.y*Math.abs(Us.y)+l.z*Math.abs(Us.z),m=e.dot(Us),p=i.dot(Us),v=r.dot(Us);if(Math.max(-Math.max(m,p,v),Math.min(m,p,v))>h)return!1}return!0}const vn=new ae,bc=new Ut;let uE=0;class Si extends Vs{constructor(e,i,r=!1){if(super(),Array.isArray(e))throw new TypeError("THREE.BufferAttribute: array should be a Typed Array.");this.isBufferAttribute=!0,Object.defineProperty(this,"id",{value:uE++}),this.name="",this.array=e,this.itemSize=i,this.count=e!==void 0?e.length/i:0,this.normalized=r,this.usage=J0,this.updateRanges=[],this.gpuType=Vi,this.version=0}onUploadCallback(){}set needsUpdate(e){e===!0&&this.version++}setUsage(e){return this.usage=e,this}addUpdateRange(e,i){this.updateRanges.push({start:e,count:i})}clearUpdateRanges(){this.updateRanges.length=0}copy(e){return this.name=e.name,this.array=new e.array.constructor(e.array),this.itemSize=e.itemSize,this.count=e.count,this.normalized=e.normalized,this.usage=e.usage,this.gpuType=e.gpuType,this}copyAt(e,i,r){e*=this.itemSize,r*=i.itemSize;for(let l=0,u=this.itemSize;l<u;l++)this.array[e+l]=i.array[r+l];return this}copyArray(e){return this.array.set(e),this}applyMatrix3(e){if(this.itemSize===2)for(let i=0,r=this.count;i<r;i++)bc.fromBufferAttribute(this,i),bc.applyMatrix3(e),this.setXY(i,bc.x,bc.y);else if(this.itemSize===3)for(let i=0,r=this.count;i<r;i++)vn.fromBufferAttribute(this,i),vn.applyMatrix3(e),this.setXYZ(i,vn.x,vn.y,vn.z);return this}applyMatrix4(e){for(let i=0,r=this.count;i<r;i++)vn.fromBufferAttribute(this,i),vn.applyMatrix4(e),this.setXYZ(i,vn.x,vn.y,vn.z);return this}applyNormalMatrix(e){for(let i=0,r=this.count;i<r;i++)vn.fromBufferAttribute(this,i),vn.applyNormalMatrix(e),this.setXYZ(i,vn.x,vn.y,vn.z);return this}transformDirection(e){for(let i=0,r=this.count;i<r;i++)vn.fromBufferAttribute(this,i),vn.transformDirection(e),this.setXYZ(i,vn.x,vn.y,vn.z);return this}set(e,i=0){return this.array.set(e,i),this}getComponent(e,i){let r=this.array[e*this.itemSize+i];return this.normalized&&(r=Pr(r,this.array)),r}setComponent(e,i,r){return this.normalized&&(r=zn(r,this.array)),this.array[e*this.itemSize+i]=r,this}getX(e){let i=this.array[e*this.itemSize];return this.normalized&&(i=Pr(i,this.array)),i}setX(e,i){return this.normalized&&(i=zn(i,this.array)),this.array[e*this.itemSize]=i,this}getY(e){let i=this.array[e*this.itemSize+1];return this.normalized&&(i=Pr(i,this.array)),i}setY(e,i){return this.normalized&&(i=zn(i,this.array)),this.array[e*this.itemSize+1]=i,this}getZ(e){let i=this.array[e*this.itemSize+2];return this.normalized&&(i=Pr(i,this.array)),i}setZ(e,i){return this.normalized&&(i=zn(i,this.array)),this.array[e*this.itemSize+2]=i,this}getW(e){let i=this.array[e*this.itemSize+3];return this.normalized&&(i=Pr(i,this.array)),i}setW(e,i){return this.normalized&&(i=zn(i,this.array)),this.array[e*this.itemSize+3]=i,this}setXY(e,i,r){return e*=this.itemSize,this.normalized&&(i=zn(i,this.array),r=zn(r,this.array)),this.array[e+0]=i,this.array[e+1]=r,this}setXYZ(e,i,r,l){return e*=this.itemSize,this.normalized&&(i=zn(i,this.array),r=zn(r,this.array),l=zn(l,this.array)),this.array[e+0]=i,this.array[e+1]=r,this.array[e+2]=l,this}setXYZW(e,i,r,l,u){return e*=this.itemSize,this.normalized&&(i=zn(i,this.array),r=zn(r,this.array),l=zn(l,this.array),u=zn(u,this.array)),this.array[e+0]=i,this.array[e+1]=r,this.array[e+2]=l,this.array[e+3]=u,this}onUpload(e){return this.onUploadCallback=e,this}clone(){return new this.constructor(this.array,this.itemSize).copy(this)}toJSON(){const e={itemSize:this.itemSize,type:this.array.constructor.name,array:Array.from(this.array),normalized:this.normalized};return this.name!==""&&(e.name=this.name),this.usage!==J0&&(e.usage=this.usage),e}dispose(){this.dispatchEvent({type:"dispose"})}}class yv extends Si{constructor(e,i,r){super(new Uint16Array(e),i,r)}}class Ev extends Si{constructor(e,i,r){super(new Uint32Array(e),i,r)}}class Ea extends Si{constructor(e,i,r){super(new Float32Array(e),i,r)}}const fE=new el,ko=new ae,Dd=new ae;class tu{constructor(e=new ae,i=-1){this.isSphere=!0,this.center=e,this.radius=i}set(e,i){return this.center.copy(e),this.radius=i,this}setFromPoints(e,i){const r=this.center;i!==void 0?r.copy(i):fE.setFromPoints(e).getCenter(r);let l=0;for(let u=0,d=e.length;u<d;u++)l=Math.max(l,r.distanceToSquared(e[u]));return this.radius=Math.sqrt(l),this}copy(e){return this.center.copy(e.center),this.radius=e.radius,this}isEmpty(){return this.radius<0}makeEmpty(){return this.center.set(0,0,0),this.radius=-1,this}containsPoint(e){return e.distanceToSquared(this.center)<=this.radius*this.radius}distanceToPoint(e){return e.distanceTo(this.center)-this.radius}intersectsSphere(e){const i=this.radius+e.radius;return e.center.distanceToSquared(this.center)<=i*i}intersectsBox(e){return e.intersectsSphere(this)}intersectsPlane(e){return Math.abs(e.distanceToPoint(this.center))<=this.radius}clampPoint(e,i){const r=this.center.distanceToSquared(e);return i.copy(e),r>this.radius*this.radius&&(i.sub(this.center).normalize(),i.multiplyScalar(this.radius).add(this.center)),i}getBoundingBox(e){return this.isEmpty()?(e.makeEmpty(),e):(e.set(this.center,this.center),e.expandByScalar(this.radius),e)}applyMatrix4(e){return this.center.applyMatrix4(e),this.radius=this.radius*e.getMaxScaleOnAxis(),this}translate(e){return this.center.add(e),this}expandByPoint(e){if(this.isEmpty())return this.center.copy(e),this.radius=0,this;ko.subVectors(e,this.center);const i=ko.lengthSq();if(i>this.radius*this.radius){const r=Math.sqrt(i),l=(r-this.radius)*.5;this.center.addScaledVector(ko,l/r),this.radius+=l}return this}union(e){return e.isEmpty()?this:this.isEmpty()?(this.copy(e),this):(this.center.equals(e.center)===!0?this.radius=Math.max(this.radius,e.radius):(Dd.subVectors(e.center,this.center).setLength(e.radius),this.expandByPoint(ko.copy(e.center).add(Dd)),this.expandByPoint(ko.copy(e.center).sub(Dd))),this)}equals(e){return e.center.equals(this.center)&&e.radius===this.radius}clone(){return new this.constructor().copy(this)}toJSON(){return{radius:this.radius,center:this.center.toArray()}}fromJSON(e){return this.radius=e.radius,this.center.fromArray(e.center),this}}let dE=0;const gi=new fn,Ud=new Zn,Ur=new ae,oi=new el,Xo=new el,bn=new ae;class Li extends Vs{constructor(){super(),this.isBufferGeometry=!0,Object.defineProperty(this,"id",{value:dE++}),this.uuid=Xr(),this.name="",this.type="BufferGeometry",this.index=null,this.indirect=null,this.indirectOffset=0,this.attributes={},this.morphAttributes={},this.morphTargetsRelative=!1,this.groups=[],this.boundingBox=null,this.boundingSphere=null,this.drawRange={start:0,count:1/0},this.userData={},this._transformed=!1}getIndex(){return this.index}setIndex(e){return Array.isArray(e)?this.index=new(Dy(e)?Ev:yv)(e,1):this.index=e,this}setIndirect(e,i=0){return this.indirect=e,this.indirectOffset=i,this}getIndirect(){return this.indirect}getAttribute(e){return this.attributes[e]}setAttribute(e,i){return this.attributes[e]=i,this}deleteAttribute(e){return delete this.attributes[e],this}hasAttribute(e){return this.attributes[e]!==void 0}addGroup(e,i,r=0){this.groups.push({start:e,count:i,materialIndex:r})}clearGroups(){this.groups=[]}setDrawRange(e,i){this.drawRange.start=e,this.drawRange.count=i}applyMatrix4(e){const i=this.attributes.position;i!==void 0&&(i.applyMatrix4(e),i.needsUpdate=!0);const r=this.attributes.normal;if(r!==void 0){const u=new st().getNormalMatrix(e);r.applyNormalMatrix(u),r.needsUpdate=!0}const l=this.attributes.tangent;return l!==void 0&&(l.transformDirection(e),l.needsUpdate=!0),this.boundingBox!==null&&this.computeBoundingBox(),this.boundingSphere!==null&&this.computeBoundingSphere(),this._transformed=!0,this}applyQuaternion(e){return gi.makeRotationFromQuaternion(e),this.applyMatrix4(gi),this}rotateX(e){return gi.makeRotationX(e),this.applyMatrix4(gi),this}rotateY(e){return gi.makeRotationY(e),this.applyMatrix4(gi),this}rotateZ(e){return gi.makeRotationZ(e),this.applyMatrix4(gi),this}translate(e,i,r){return gi.makeTranslation(e,i,r),this.applyMatrix4(gi),this}scale(e,i,r){return gi.makeScale(e,i,r),this.applyMatrix4(gi),this}lookAt(e){return Ud.lookAt(e),Ud.updateMatrix(),this.applyMatrix4(Ud.matrix),this}center(){return this.computeBoundingBox(),this.boundingBox.getCenter(Ur).negate(),this.translate(Ur.x,Ur.y,Ur.z),this}setFromPoints(e){const i=this.getAttribute("position");if(i===void 0){const r=[];for(let l=0,u=e.length;l<u;l++){const d=e[l];r.push(d.x,d.y,d.z||0)}this.setAttribute("position",new Ea(r,3))}else{const r=Math.min(e.length,i.count);for(let l=0;l<r;l++){const u=e[l];i.setXYZ(l,u.x,u.y,u.z||0)}e.length>i.count&&et("BufferGeometry: Buffer size too small for points data. Use .dispose() and create a new geometry."),i.needsUpdate=!0}return this}computeBoundingBox(){this.boundingBox===null&&(this.boundingBox=new el);const e=this.attributes.position,i=this.morphAttributes.position;if(e&&e.isGLBufferAttribute){bt("BufferGeometry.computeBoundingBox(): GLBufferAttribute requires a manual bounding box.",this),this.boundingBox.set(new ae(-1/0,-1/0,-1/0),new ae(1/0,1/0,1/0));return}if(e!==void 0){if(this.boundingBox.setFromBufferAttribute(e),i)for(let r=0,l=i.length;r<l;r++){const u=i[r];oi.setFromBufferAttribute(u),this.morphTargetsRelative?(bn.addVectors(this.boundingBox.min,oi.min),this.boundingBox.expandByPoint(bn),bn.addVectors(this.boundingBox.max,oi.max),this.boundingBox.expandByPoint(bn)):(this.boundingBox.expandByPoint(oi.min),this.boundingBox.expandByPoint(oi.max))}}else this.boundingBox.makeEmpty();(isNaN(this.boundingBox.min.x)||isNaN(this.boundingBox.min.y)||isNaN(this.boundingBox.min.z))&&bt('BufferGeometry.computeBoundingBox(): Computed min/max have NaN values. The "position" attribute is likely to have NaN values.',this)}computeBoundingSphere(){this.boundingSphere===null&&(this.boundingSphere=new tu);const e=this.attributes.position,i=this.morphAttributes.position;if(e&&e.isGLBufferAttribute){bt("BufferGeometry.computeBoundingSphere(): GLBufferAttribute requires a manual bounding sphere.",this),this.boundingSphere.set(new ae,1/0);return}if(e){const r=this.boundingSphere.center;if(oi.setFromBufferAttribute(e),i)for(let u=0,d=i.length;u<d;u++){const h=i[u];Xo.setFromBufferAttribute(h),this.morphTargetsRelative?(bn.addVectors(oi.min,Xo.min),oi.expandByPoint(bn),bn.addVectors(oi.max,Xo.max),oi.expandByPoint(bn)):(oi.expandByPoint(Xo.min),oi.expandByPoint(Xo.max))}oi.getCenter(r);let l=0;for(let u=0,d=e.count;u<d;u++)bn.fromBufferAttribute(e,u),l=Math.max(l,r.distanceToSquared(bn));if(i)for(let u=0,d=i.length;u<d;u++){const h=i[u],m=this.morphTargetsRelative;for(let p=0,v=h.count;p<v;p++)bn.fromBufferAttribute(h,p),m&&(Ur.fromBufferAttribute(e,p),bn.add(Ur)),l=Math.max(l,r.distanceToSquared(bn))}this.boundingSphere.radius=Math.sqrt(l),isNaN(this.boundingSphere.radius)&&bt('BufferGeometry.computeBoundingSphere(): Computed radius is NaN. The "position" attribute is likely to have NaN values.',this)}}computeTangents(){const e=this.index,i=this.attributes;if(e===null||i.position===void 0||i.normal===void 0||i.uv===void 0){bt("BufferGeometry: .computeTangents() failed. Missing required attributes (index, position, normal or uv)");return}const r=i.position,l=i.normal,u=i.uv;let d=this.getAttribute("tangent");(d===void 0||d.count!==r.count)&&(d=new Si(new Float32Array(4*r.count),4),this.setAttribute("tangent",d));const h=[],m=[];for(let b=0;b<r.count;b++)h[b]=new ae,m[b]=new ae;const p=new ae,v=new ae,x=new ae,g=new Ut,E=new Ut,T=new Ut,D=new ae,y=new ae;function S(b,L,W){p.fromBufferAttribute(r,b),v.fromBufferAttribute(r,L),x.fromBufferAttribute(r,W),g.fromBufferAttribute(u,b),E.fromBufferAttribute(u,L),T.fromBufferAttribute(u,W),v.sub(p),x.sub(p),E.sub(g),T.sub(g);const V=1/(E.x*T.y-T.x*E.y);isFinite(V)&&(D.copy(v).multiplyScalar(T.y).addScaledVector(x,-E.y).multiplyScalar(V),y.copy(x).multiplyScalar(E.x).addScaledVector(v,-T.x).multiplyScalar(V),h[b].add(D),h[L].add(D),h[W].add(D),m[b].add(y),m[L].add(y),m[W].add(y))}let z=this.groups;z.length===0&&(z=[{start:0,count:e.count}]);for(let b=0,L=z.length;b<L;++b){const W=z[b],V=W.start,Z=W.count;for(let de=V,he=V+Z;de<he;de+=3)S(e.getX(de+0),e.getX(de+1),e.getX(de+2))}const F=new ae,C=new ae,O=new ae,U=new ae;function B(b){O.fromBufferAttribute(l,b),U.copy(O);const L=h[b];F.copy(L),F.sub(O.multiplyScalar(O.dot(L))).normalize(),C.crossVectors(U,L);const V=C.dot(m[b])<0?-1:1;d.setXYZW(b,F.x,F.y,F.z,V)}for(let b=0,L=z.length;b<L;++b){const W=z[b],V=W.start,Z=W.count;for(let de=V,he=V+Z;de<he;de+=3)B(e.getX(de+0)),B(e.getX(de+1)),B(e.getX(de+2))}this._transformed=!0}computeVertexNormals(){const e=this.index,i=this.getAttribute("position");if(i!==void 0){let r=this.getAttribute("normal");if(r===void 0||r.count!==i.count)r=new Si(new Float32Array(i.count*3),3),this.setAttribute("normal",r);else for(let g=0,E=r.count;g<E;g++)r.setXYZ(g,0,0,0);const l=new ae,u=new ae,d=new ae,h=new ae,m=new ae,p=new ae,v=new ae,x=new ae;if(e)for(let g=0,E=e.count;g<E;g+=3){const T=e.getX(g+0),D=e.getX(g+1),y=e.getX(g+2);l.fromBufferAttribute(i,T),u.fromBufferAttribute(i,D),d.fromBufferAttribute(i,y),v.subVectors(d,u),x.subVectors(l,u),v.cross(x),h.fromBufferAttribute(r,T),m.fromBufferAttribute(r,D),p.fromBufferAttribute(r,y),h.add(v),m.add(v),p.add(v),r.setXYZ(T,h.x,h.y,h.z),r.setXYZ(D,m.x,m.y,m.z),r.setXYZ(y,p.x,p.y,p.z)}else for(let g=0,E=i.count;g<E;g+=3)l.fromBufferAttribute(i,g+0),u.fromBufferAttribute(i,g+1),d.fromBufferAttribute(i,g+2),v.subVectors(d,u),x.subVectors(l,u),v.cross(x),r.setXYZ(g+0,v.x,v.y,v.z),r.setXYZ(g+1,v.x,v.y,v.z),r.setXYZ(g+2,v.x,v.y,v.z);this.normalizeNormals(),r.needsUpdate=!0}}normalizeNormals(){const e=this.attributes.normal;for(let i=0,r=e.count;i<r;i++)bn.fromBufferAttribute(e,i),bn.normalize(),e.setXYZ(i,bn.x,bn.y,bn.z)}toNonIndexed(){function e(h,m){const p=h.array,v=h.itemSize,x=h.normalized,g=new p.constructor(m.length*v);let E=0,T=0;for(let D=0,y=m.length;D<y;D++){h.isInterleavedBufferAttribute?E=m[D]*h.data.stride+h.offset:E=m[D]*v;for(let S=0;S<v;S++)g[T++]=p[E++]}return new Si(g,v,x)}if(this.index===null)return et("BufferGeometry.toNonIndexed(): BufferGeometry is already non-indexed."),this;const i=new Li,r=this.index.array,l=this.attributes;for(const h in l){const m=l[h],p=e(m,r);i.setAttribute(h,p)}const u=this.morphAttributes;for(const h in u){const m=[],p=u[h];for(let v=0,x=p.length;v<x;v++){const g=p[v],E=e(g,r);m.push(E)}i.morphAttributes[h]=m}i.morphTargetsRelative=this.morphTargetsRelative;const d=this.groups;for(let h=0,m=d.length;h<m;h++){const p=d[h];i.addGroup(p.start,p.count,p.materialIndex)}return i}toJSON(){const e={metadata:{version:4.7,type:"BufferGeometry",generator:"BufferGeometry.toJSON"}};if(e.uuid=this.uuid,e.type=this.parameters!==void 0&&this._transformed===!0?"BufferGeometry":this.type,this.name!==""&&(e.name=this.name),Object.keys(this.userData).length>0&&(e.userData=this.userData),this.parameters!==void 0&&this._transformed!==!0){const m=this.parameters;for(const p in m)m[p]!==void 0&&(e[p]=m[p]);return e}e.data={attributes:{}};const i=this.index;i!==null&&(e.data.index={type:i.array.constructor.name,array:Array.prototype.slice.call(i.array)});const r=this.attributes;for(const m in r){const p=r[m];e.data.attributes[m]=p.toJSON(e.data)}const l={};let u=!1;for(const m in this.morphAttributes){const p=this.morphAttributes[m],v=[];for(let x=0,g=p.length;x<g;x++){const E=p[x];v.push(E.toJSON(e.data))}v.length>0&&(l[m]=v,u=!0)}u&&(e.data.morphAttributes=l,e.data.morphTargetsRelative=this.morphTargetsRelative);const d=this.groups;d.length>0&&(e.data.groups=JSON.parse(JSON.stringify(d)));const h=this.boundingSphere;return h!==null&&(e.data.boundingSphere=h.toJSON()),e}clone(){return new this.constructor().copy(this)}copy(e){this.index=null,this.attributes={},this.morphAttributes={},this.groups=[],this.boundingBox=null,this.boundingSphere=null;const i={};this.name=e.name;const r=e.index;r!==null&&this.setIndex(r.clone());const l=e.attributes;for(const p in l){const v=l[p];this.setAttribute(p,v.clone(i))}const u=e.morphAttributes;for(const p in u){const v=[],x=u[p];for(let g=0,E=x.length;g<E;g++)v.push(x[g].clone(i));this.morphAttributes[p]=v}this.morphTargetsRelative=e.morphTargetsRelative;const d=e.groups;for(let p=0,v=d.length;p<v;p++){const x=d[p];this.addGroup(x.start,x.count,x.materialIndex)}const h=e.boundingBox;h!==null&&(this.boundingBox=h.clone());const m=e.boundingSphere;return m!==null&&(this.boundingSphere=m.clone()),this.drawRange.start=e.drawRange.start,this.drawRange.count=e.drawRange.count,this.userData=e.userData,this._transformed=e._transformed,this}dispose(){this.dispatchEvent({type:"dispose"})}}let hE=0;class tl extends Vs{constructor(){super(),this.isMaterial=!0,Object.defineProperty(this,"id",{value:hE++}),this.uuid=Xr(),this.name="",this.type="Material",this.blending=Fr,this.side=ls,this.vertexColors=!1,this.opacity=1,this.transparent=!1,this.alphaHash=!1,this.blendSrc=Zd,this.blendDst=Kd,this.blendEquation=Ps,this.blendSrcAlpha=null,this.blendDstAlpha=null,this.blendEquationAlpha=null,this.blendColor=new Et(0,0,0),this.blendAlpha=0,this.depthFunc=Hr,this.depthTest=!0,this.depthWrite=!0,this.stencilWriteMask=255,this.stencilFunc=Q0,this.stencilRef=0,this.stencilFuncMask=255,this.stencilFail=Mr,this.stencilZFail=Mr,this.stencilZPass=Mr,this.stencilWrite=!1,this.clippingPlanes=null,this.clipIntersection=!1,this.clipShadows=!1,this.shadowSide=null,this.colorWrite=!0,this.precision=null,this.polygonOffset=!1,this.polygonOffsetFactor=0,this.polygonOffsetUnits=0,this.dithering=!1,this.alphaToCoverage=!1,this.premultipliedAlpha=!1,this.forceSinglePass=!1,this.allowOverride=!0,this.visible=!0,this.toneMapped=!0,this.userData={},this.version=0,this._alphaTest=0}get alphaTest(){return this._alphaTest}set alphaTest(e){this._alphaTest>0!=e>0&&this.version++,this._alphaTest=e}onBeforeRender(){}onBeforeCompile(){}customProgramCacheKey(){return this.onBeforeCompile.toString()}setValues(e){if(e!==void 0)for(const i in e){const r=e[i];if(r===void 0){et(`Material: parameter '${i}' has value of undefined.`);continue}const l=this[i];if(l===void 0){et(`Material: '${i}' is not a property of THREE.${this.type}.`);continue}l&&l.isColor?l.set(r):l&&l.isVector2&&r&&r.isVector2||l&&l.isEuler&&r&&r.isEuler||l&&l.isVector3&&r&&r.isVector3?l.copy(r):this[i]=r}}toJSON(e){const i=e===void 0||typeof e=="string";i&&(e={textures:{},images:{}});const r={metadata:{version:4.7,type:"Material",generator:"Material.toJSON"}};r.uuid=this.uuid,r.type=this.type,this.name!==""&&(r.name=this.name),this.color&&this.color.isColor&&(r.color=this.color.getHex()),this.roughness!==void 0&&(r.roughness=this.roughness),this.metalness!==void 0&&(r.metalness=this.metalness),this.sheen!==void 0&&(r.sheen=this.sheen),this.sheenColor&&this.sheenColor.isColor&&(r.sheenColor=this.sheenColor.getHex()),this.sheenRoughness!==void 0&&(r.sheenRoughness=this.sheenRoughness),this.emissive&&this.emissive.isColor&&(r.emissive=this.emissive.getHex()),this.emissiveIntensity!==void 0&&this.emissiveIntensity!==1&&(r.emissiveIntensity=this.emissiveIntensity),this.specular&&this.specular.isColor&&(r.specular=this.specular.getHex()),this.specularIntensity!==void 0&&(r.specularIntensity=this.specularIntensity),this.specularColor&&this.specularColor.isColor&&(r.specularColor=this.specularColor.getHex()),this.shininess!==void 0&&(r.shininess=this.shininess),this.clearcoat!==void 0&&(r.clearcoat=this.clearcoat),this.clearcoatRoughness!==void 0&&(r.clearcoatRoughness=this.clearcoatRoughness),this.clearcoatMap&&this.clearcoatMap.isTexture&&(r.clearcoatMap=this.clearcoatMap.toJSON(e).uuid),this.clearcoatRoughnessMap&&this.clearcoatRoughnessMap.isTexture&&(r.clearcoatRoughnessMap=this.clearcoatRoughnessMap.toJSON(e).uuid),this.clearcoatNormalMap&&this.clearcoatNormalMap.isTexture&&(r.clearcoatNormalMap=this.clearcoatNormalMap.toJSON(e).uuid,r.clearcoatNormalScale=this.clearcoatNormalScale.toArray()),this.sheenColorMap&&this.sheenColorMap.isTexture&&(r.sheenColorMap=this.sheenColorMap.toJSON(e).uuid),this.sheenRoughnessMap&&this.sheenRoughnessMap.isTexture&&(r.sheenRoughnessMap=this.sheenRoughnessMap.toJSON(e).uuid),this.dispersion!==void 0&&(r.dispersion=this.dispersion),this.iridescence!==void 0&&(r.iridescence=this.iridescence),this.iridescenceIOR!==void 0&&(r.iridescenceIOR=this.iridescenceIOR),this.iridescenceThicknessRange!==void 0&&(r.iridescenceThicknessRange=this.iridescenceThicknessRange),this.iridescenceMap&&this.iridescenceMap.isTexture&&(r.iridescenceMap=this.iridescenceMap.toJSON(e).uuid),this.iridescenceThicknessMap&&this.iridescenceThicknessMap.isTexture&&(r.iridescenceThicknessMap=this.iridescenceThicknessMap.toJSON(e).uuid),this.anisotropy!==void 0&&(r.anisotropy=this.anisotropy),this.anisotropyRotation!==void 0&&(r.anisotropyRotation=this.anisotropyRotation),this.anisotropyMap&&this.anisotropyMap.isTexture&&(r.anisotropyMap=this.anisotropyMap.toJSON(e).uuid),this.map&&this.map.isTexture&&(r.map=this.map.toJSON(e).uuid),this.matcap&&this.matcap.isTexture&&(r.matcap=this.matcap.toJSON(e).uuid),this.alphaMap&&this.alphaMap.isTexture&&(r.alphaMap=this.alphaMap.toJSON(e).uuid),this.lightMap&&this.lightMap.isTexture&&(r.lightMap=this.lightMap.toJSON(e).uuid,r.lightMapIntensity=this.lightMapIntensity),this.aoMap&&this.aoMap.isTexture&&(r.aoMap=this.aoMap.toJSON(e).uuid,r.aoMapIntensity=this.aoMapIntensity),this.bumpMap&&this.bumpMap.isTexture&&(r.bumpMap=this.bumpMap.toJSON(e).uuid,r.bumpScale=this.bumpScale),this.normalMap&&this.normalMap.isTexture&&(r.normalMap=this.normalMap.toJSON(e).uuid,r.normalMapType=this.normalMapType,r.normalScale=this.normalScale.toArray()),this.displacementMap&&this.displacementMap.isTexture&&(r.displacementMap=this.displacementMap.toJSON(e).uuid,r.displacementScale=this.displacementScale,r.displacementBias=this.displacementBias),this.roughnessMap&&this.roughnessMap.isTexture&&(r.roughnessMap=this.roughnessMap.toJSON(e).uuid),this.metalnessMap&&this.metalnessMap.isTexture&&(r.metalnessMap=this.metalnessMap.toJSON(e).uuid),this.emissiveMap&&this.emissiveMap.isTexture&&(r.emissiveMap=this.emissiveMap.toJSON(e).uuid),this.specularMap&&this.specularMap.isTexture&&(r.specularMap=this.specularMap.toJSON(e).uuid),this.specularIntensityMap&&this.specularIntensityMap.isTexture&&(r.specularIntensityMap=this.specularIntensityMap.toJSON(e).uuid),this.specularColorMap&&this.specularColorMap.isTexture&&(r.specularColorMap=this.specularColorMap.toJSON(e).uuid),this.envMap&&this.envMap.isTexture&&(r.envMap=this.envMap.toJSON(e).uuid,this.combine!==void 0&&(r.combine=this.combine)),this.envMapRotation!==void 0&&(r.envMapRotation=this.envMapRotation.toArray()),this.envMapIntensity!==void 0&&(r.envMapIntensity=this.envMapIntensity),this.reflectivity!==void 0&&(r.reflectivity=this.reflectivity),this.refractionRatio!==void 0&&(r.refractionRatio=this.refractionRatio),this.gradientMap&&this.gradientMap.isTexture&&(r.gradientMap=this.gradientMap.toJSON(e).uuid),this.transmission!==void 0&&(r.transmission=this.transmission),this.transmissionMap&&this.transmissionMap.isTexture&&(r.transmissionMap=this.transmissionMap.toJSON(e).uuid),this.thickness!==void 0&&(r.thickness=this.thickness),this.thicknessMap&&this.thicknessMap.isTexture&&(r.thicknessMap=this.thicknessMap.toJSON(e).uuid),this.attenuationDistance!==void 0&&this.attenuationDistance!==1/0&&(r.attenuationDistance=this.attenuationDistance),this.attenuationColor!==void 0&&(r.attenuationColor=this.attenuationColor.getHex()),this.size!==void 0&&(r.size=this.size),this.shadowSide!==null&&(r.shadowSide=this.shadowSide),this.sizeAttenuation!==void 0&&(r.sizeAttenuation=this.sizeAttenuation),this.blending!==Fr&&(r.blending=this.blending),this.side!==ls&&(r.side=this.side),this.vertexColors===!0&&(r.vertexColors=!0),this.opacity<1&&(r.opacity=this.opacity),this.transparent===!0&&(r.transparent=!0),this.blendSrc!==Zd&&(r.blendSrc=this.blendSrc),this.blendDst!==Kd&&(r.blendDst=this.blendDst),this.blendEquation!==Ps&&(r.blendEquation=this.blendEquation),this.blendSrcAlpha!==null&&(r.blendSrcAlpha=this.blendSrcAlpha),this.blendDstAlpha!==null&&(r.blendDstAlpha=this.blendDstAlpha),this.blendEquationAlpha!==null&&(r.blendEquationAlpha=this.blendEquationAlpha),this.blendColor&&this.blendColor.isColor&&(r.blendColor=this.blendColor.getHex()),this.blendAlpha!==0&&(r.blendAlpha=this.blendAlpha),this.depthFunc!==Hr&&(r.depthFunc=this.depthFunc),this.depthTest===!1&&(r.depthTest=this.depthTest),this.depthWrite===!1&&(r.depthWrite=this.depthWrite),this.colorWrite===!1&&(r.colorWrite=this.colorWrite),this.stencilWriteMask!==255&&(r.stencilWriteMask=this.stencilWriteMask),this.stencilFunc!==Q0&&(r.stencilFunc=this.stencilFunc),this.stencilRef!==0&&(r.stencilRef=this.stencilRef),this.stencilFuncMask!==255&&(r.stencilFuncMask=this.stencilFuncMask),this.stencilFail!==Mr&&(r.stencilFail=this.stencilFail),this.stencilZFail!==Mr&&(r.stencilZFail=this.stencilZFail),this.stencilZPass!==Mr&&(r.stencilZPass=this.stencilZPass),this.stencilWrite===!0&&(r.stencilWrite=this.stencilWrite),this.rotation!==void 0&&this.rotation!==0&&(r.rotation=this.rotation),this.polygonOffset===!0&&(r.polygonOffset=!0),this.polygonOffsetFactor!==0&&(r.polygonOffsetFactor=this.polygonOffsetFactor),this.polygonOffsetUnits!==0&&(r.polygonOffsetUnits=this.polygonOffsetUnits),this.linewidth!==void 0&&this.linewidth!==1&&(r.linewidth=this.linewidth),this.dashSize!==void 0&&(r.dashSize=this.dashSize),this.gapSize!==void 0&&(r.gapSize=this.gapSize),this.scale!==void 0&&(r.scale=this.scale),this.dithering===!0&&(r.dithering=!0),this.alphaTest>0&&(r.alphaTest=this.alphaTest),this.alphaHash===!0&&(r.alphaHash=!0),this.alphaToCoverage===!0&&(r.alphaToCoverage=!0),this.premultipliedAlpha===!0&&(r.premultipliedAlpha=!0),this.forceSinglePass===!0&&(r.forceSinglePass=!0),this.allowOverride===!1&&(r.allowOverride=!1),this.wireframe===!0&&(r.wireframe=!0),this.wireframeLinewidth>1&&(r.wireframeLinewidth=this.wireframeLinewidth),this.wireframeLinecap!=="round"&&(r.wireframeLinecap=this.wireframeLinecap),this.wireframeLinejoin!=="round"&&(r.wireframeLinejoin=this.wireframeLinejoin),this.flatShading===!0&&(r.flatShading=!0),this.visible===!1&&(r.visible=!1),this.toneMapped===!1&&(r.toneMapped=!1),this.fog===!1&&(r.fog=!1),Object.keys(this.userData).length>0&&(r.userData=this.userData);function l(u){const d=[];for(const h in u){const m=u[h];delete m.metadata,d.push(m)}return d}if(i){const u=l(e.textures),d=l(e.images);u.length>0&&(r.textures=u),d.length>0&&(r.images=d)}return r}fromJSON(e,i){if(e.uuid!==void 0&&(this.uuid=e.uuid),e.name!==void 0&&(this.name=e.name),e.color!==void 0&&this.color!==void 0&&this.color.setHex(e.color),e.roughness!==void 0&&(this.roughness=e.roughness),e.metalness!==void 0&&(this.metalness=e.metalness),e.sheen!==void 0&&(this.sheen=e.sheen),e.sheenColor!==void 0&&(this.sheenColor=new Et().setHex(e.sheenColor)),e.sheenRoughness!==void 0&&(this.sheenRoughness=e.sheenRoughness),e.emissive!==void 0&&this.emissive!==void 0&&this.emissive.setHex(e.emissive),e.specular!==void 0&&this.specular!==void 0&&this.specular.setHex(e.specular),e.specularIntensity!==void 0&&(this.specularIntensity=e.specularIntensity),e.specularColor!==void 0&&this.specularColor!==void 0&&this.specularColor.setHex(e.specularColor),e.shininess!==void 0&&(this.shininess=e.shininess),e.clearcoat!==void 0&&(this.clearcoat=e.clearcoat),e.clearcoatRoughness!==void 0&&(this.clearcoatRoughness=e.clearcoatRoughness),e.dispersion!==void 0&&(this.dispersion=e.dispersion),e.iridescence!==void 0&&(this.iridescence=e.iridescence),e.iridescenceIOR!==void 0&&(this.iridescenceIOR=e.iridescenceIOR),e.iridescenceThicknessRange!==void 0&&(this.iridescenceThicknessRange=e.iridescenceThicknessRange),e.transmission!==void 0&&(this.transmission=e.transmission),e.thickness!==void 0&&(this.thickness=e.thickness),e.attenuationDistance!==void 0&&(this.attenuationDistance=e.attenuationDistance),e.attenuationColor!==void 0&&this.attenuationColor!==void 0&&this.attenuationColor.setHex(e.attenuationColor),e.anisotropy!==void 0&&(this.anisotropy=e.anisotropy),e.anisotropyRotation!==void 0&&(this.anisotropyRotation=e.anisotropyRotation),e.fog!==void 0&&(this.fog=e.fog),e.flatShading!==void 0&&(this.flatShading=e.flatShading),e.blending!==void 0&&(this.blending=e.blending),e.combine!==void 0&&(this.combine=e.combine),e.side!==void 0&&(this.side=e.side),e.shadowSide!==void 0&&(this.shadowSide=e.shadowSide),e.opacity!==void 0&&(this.opacity=e.opacity),e.transparent!==void 0&&(this.transparent=e.transparent),e.alphaTest!==void 0&&(this.alphaTest=e.alphaTest),e.alphaHash!==void 0&&(this.alphaHash=e.alphaHash),e.depthFunc!==void 0&&(this.depthFunc=e.depthFunc),e.depthTest!==void 0&&(this.depthTest=e.depthTest),e.depthWrite!==void 0&&(this.depthWrite=e.depthWrite),e.colorWrite!==void 0&&(this.colorWrite=e.colorWrite),e.blendSrc!==void 0&&(this.blendSrc=e.blendSrc),e.blendDst!==void 0&&(this.blendDst=e.blendDst),e.blendEquation!==void 0&&(this.blendEquation=e.blendEquation),e.blendSrcAlpha!==void 0&&(this.blendSrcAlpha=e.blendSrcAlpha),e.blendDstAlpha!==void 0&&(this.blendDstAlpha=e.blendDstAlpha),e.blendEquationAlpha!==void 0&&(this.blendEquationAlpha=e.blendEquationAlpha),e.blendColor!==void 0&&this.blendColor!==void 0&&this.blendColor.setHex(e.blendColor),e.blendAlpha!==void 0&&(this.blendAlpha=e.blendAlpha),e.stencilWriteMask!==void 0&&(this.stencilWriteMask=e.stencilWriteMask),e.stencilFunc!==void 0&&(this.stencilFunc=e.stencilFunc),e.stencilRef!==void 0&&(this.stencilRef=e.stencilRef),e.stencilFuncMask!==void 0&&(this.stencilFuncMask=e.stencilFuncMask),e.stencilFail!==void 0&&(this.stencilFail=e.stencilFail),e.stencilZFail!==void 0&&(this.stencilZFail=e.stencilZFail),e.stencilZPass!==void 0&&(this.stencilZPass=e.stencilZPass),e.stencilWrite!==void 0&&(this.stencilWrite=e.stencilWrite),e.wireframe!==void 0&&(this.wireframe=e.wireframe),e.wireframeLinewidth!==void 0&&(this.wireframeLinewidth=e.wireframeLinewidth),e.wireframeLinecap!==void 0&&(this.wireframeLinecap=e.wireframeLinecap),e.wireframeLinejoin!==void 0&&(this.wireframeLinejoin=e.wireframeLinejoin),e.rotation!==void 0&&(this.rotation=e.rotation),e.linewidth!==void 0&&(this.linewidth=e.linewidth),e.dashSize!==void 0&&(this.dashSize=e.dashSize),e.gapSize!==void 0&&(this.gapSize=e.gapSize),e.scale!==void 0&&(this.scale=e.scale),e.polygonOffset!==void 0&&(this.polygonOffset=e.polygonOffset),e.polygonOffsetFactor!==void 0&&(this.polygonOffsetFactor=e.polygonOffsetFactor),e.polygonOffsetUnits!==void 0&&(this.polygonOffsetUnits=e.polygonOffsetUnits),e.dithering!==void 0&&(this.dithering=e.dithering),e.alphaToCoverage!==void 0&&(this.alphaToCoverage=e.alphaToCoverage),e.premultipliedAlpha!==void 0&&(this.premultipliedAlpha=e.premultipliedAlpha),e.forceSinglePass!==void 0&&(this.forceSinglePass=e.forceSinglePass),e.allowOverride!==void 0&&(this.allowOverride=e.allowOverride),e.visible!==void 0&&(this.visible=e.visible),e.toneMapped!==void 0&&(this.toneMapped=e.toneMapped),e.userData!==void 0&&(this.userData=e.userData),e.vertexColors!==void 0&&(typeof e.vertexColors=="number"?this.vertexColors=e.vertexColors>0:this.vertexColors=e.vertexColors),e.size!==void 0&&(this.size=e.size),e.sizeAttenuation!==void 0&&(this.sizeAttenuation=e.sizeAttenuation),e.map!==void 0&&(this.map=i[e.map]||null),e.matcap!==void 0&&(this.matcap=i[e.matcap]||null),e.alphaMap!==void 0&&(this.alphaMap=i[e.alphaMap]||null),e.bumpMap!==void 0&&(this.bumpMap=i[e.bumpMap]||null),e.bumpScale!==void 0&&(this.bumpScale=e.bumpScale),e.normalMap!==void 0&&(this.normalMap=i[e.normalMap]||null),e.normalMapType!==void 0&&(this.normalMapType=e.normalMapType),e.normalScale!==void 0){let r=e.normalScale;Array.isArray(r)===!1&&(r=[r,r]),this.normalScale=new Ut().fromArray(r)}return e.displacementMap!==void 0&&(this.displacementMap=i[e.displacementMap]||null),e.displacementScale!==void 0&&(this.displacementScale=e.displacementScale),e.displacementBias!==void 0&&(this.displacementBias=e.displacementBias),e.roughnessMap!==void 0&&(this.roughnessMap=i[e.roughnessMap]||null),e.metalnessMap!==void 0&&(this.metalnessMap=i[e.metalnessMap]||null),e.emissiveMap!==void 0&&(this.emissiveMap=i[e.emissiveMap]||null),e.emissiveIntensity!==void 0&&(this.emissiveIntensity=e.emissiveIntensity),e.specularMap!==void 0&&(this.specularMap=i[e.specularMap]||null),e.specularIntensityMap!==void 0&&(this.specularIntensityMap=i[e.specularIntensityMap]||null),e.specularColorMap!==void 0&&(this.specularColorMap=i[e.specularColorMap]||null),e.envMap!==void 0&&(this.envMap=i[e.envMap]||null),e.envMapRotation!==void 0&&this.envMapRotation.fromArray(e.envMapRotation),e.envMapIntensity!==void 0&&(this.envMapIntensity=e.envMapIntensity),e.reflectivity!==void 0&&(this.reflectivity=e.reflectivity),e.refractionRatio!==void 0&&(this.refractionRatio=e.refractionRatio),e.lightMap!==void 0&&(this.lightMap=i[e.lightMap]||null),e.lightMapIntensity!==void 0&&(this.lightMapIntensity=e.lightMapIntensity),e.aoMap!==void 0&&(this.aoMap=i[e.aoMap]||null),e.aoMapIntensity!==void 0&&(this.aoMapIntensity=e.aoMapIntensity),e.gradientMap!==void 0&&(this.gradientMap=i[e.gradientMap]||null),e.clearcoatMap!==void 0&&(this.clearcoatMap=i[e.clearcoatMap]||null),e.clearcoatRoughnessMap!==void 0&&(this.clearcoatRoughnessMap=i[e.clearcoatRoughnessMap]||null),e.clearcoatNormalMap!==void 0&&(this.clearcoatNormalMap=i[e.clearcoatNormalMap]||null),e.clearcoatNormalScale!==void 0&&(this.clearcoatNormalScale=new Ut().fromArray(e.clearcoatNormalScale)),e.iridescenceMap!==void 0&&(this.iridescenceMap=i[e.iridescenceMap]||null),e.iridescenceThicknessMap!==void 0&&(this.iridescenceThicknessMap=i[e.iridescenceThicknessMap]||null),e.transmissionMap!==void 0&&(this.transmissionMap=i[e.transmissionMap]||null),e.thicknessMap!==void 0&&(this.thicknessMap=i[e.thicknessMap]||null),e.anisotropyMap!==void 0&&(this.anisotropyMap=i[e.anisotropyMap]||null),e.sheenColorMap!==void 0&&(this.sheenColorMap=i[e.sheenColorMap]||null),e.sheenRoughnessMap!==void 0&&(this.sheenRoughnessMap=i[e.sheenRoughnessMap]||null),this}clone(){return new this.constructor().copy(this)}copy(e){this.name=e.name,this.blending=e.blending,this.side=e.side,this.vertexColors=e.vertexColors,this.opacity=e.opacity,this.transparent=e.transparent,this.blendSrc=e.blendSrc,this.blendDst=e.blendDst,this.blendEquation=e.blendEquation,this.blendSrcAlpha=e.blendSrcAlpha,this.blendDstAlpha=e.blendDstAlpha,this.blendEquationAlpha=e.blendEquationAlpha,this.blendColor.copy(e.blendColor),this.blendAlpha=e.blendAlpha,this.depthFunc=e.depthFunc,this.depthTest=e.depthTest,this.depthWrite=e.depthWrite,this.stencilWriteMask=e.stencilWriteMask,this.stencilFunc=e.stencilFunc,this.stencilRef=e.stencilRef,this.stencilFuncMask=e.stencilFuncMask,this.stencilFail=e.stencilFail,this.stencilZFail=e.stencilZFail,this.stencilZPass=e.stencilZPass,this.stencilWrite=e.stencilWrite;const i=e.clippingPlanes;let r=null;if(i!==null){const l=i.length;r=new Array(l);for(let u=0;u!==l;++u)r[u]=i[u].clone()}return this.clippingPlanes=r,this.clipIntersection=e.clipIntersection,this.clipShadows=e.clipShadows,this.shadowSide=e.shadowSide,this.colorWrite=e.colorWrite,this.precision=e.precision,this.polygonOffset=e.polygonOffset,this.polygonOffsetFactor=e.polygonOffsetFactor,this.polygonOffsetUnits=e.polygonOffsetUnits,this.dithering=e.dithering,this.alphaTest=e.alphaTest,this.alphaHash=e.alphaHash,this.alphaToCoverage=e.alphaToCoverage,this.premultipliedAlpha=e.premultipliedAlpha,this.forceSinglePass=e.forceSinglePass,this.allowOverride=e.allowOverride,this.visible=e.visible,this.toneMapped=e.toneMapped,this.userData=JSON.parse(JSON.stringify(e.userData)),this}dispose(){this.dispatchEvent({type:"dispose"})}set needsUpdate(e){e===!0&&this.version++}}const va=new ae,Ld=new ae,Tc=new ae,as=new ae,Nd=new ae,Ac=new ae,Od=new ae;class bv{constructor(e=new ae,i=new ae(0,0,-1)){this.origin=e,this.direction=i}set(e,i){return this.origin.copy(e),this.direction.copy(i),this}copy(e){return this.origin.copy(e.origin),this.direction.copy(e.direction),this}at(e,i){return i.copy(this.origin).addScaledVector(this.direction,e)}lookAt(e){return this.direction.copy(e).sub(this.origin).normalize(),this}recast(e){return this.origin.copy(this.at(e,va)),this}closestPointToPoint(e,i){i.subVectors(e,this.origin);const r=i.dot(this.direction);return r<0?i.copy(this.origin):i.copy(this.origin).addScaledVector(this.direction,r)}distanceToPoint(e){return Math.sqrt(this.distanceSqToPoint(e))}distanceSqToPoint(e){const i=va.subVectors(e,this.origin).dot(this.direction);return i<0?this.origin.distanceToSquared(e):(va.copy(this.origin).addScaledVector(this.direction,i),va.distanceToSquared(e))}distanceSqToSegment(e,i,r,l){Ld.copy(e).add(i).multiplyScalar(.5),Tc.copy(i).sub(e).normalize(),as.copy(this.origin).sub(Ld);const u=e.distanceTo(i)*.5,d=-this.direction.dot(Tc),h=as.dot(this.direction),m=-as.dot(Tc),p=as.lengthSq(),v=Math.abs(1-d*d);let x,g,E,T;if(v>0)if(x=d*m-h,g=d*h-m,T=u*v,x>=0)if(g>=-T)if(g<=T){const D=1/v;x*=D,g*=D,E=x*(x+d*g+2*h)+g*(d*x+g+2*m)+p}else g=u,x=Math.max(0,-(d*g+h)),E=-x*x+g*(g+2*m)+p;else g=-u,x=Math.max(0,-(d*g+h)),E=-x*x+g*(g+2*m)+p;else g<=-T?(x=Math.max(0,-(-d*u+h)),g=x>0?-u:Math.min(Math.max(-u,-m),u),E=-x*x+g*(g+2*m)+p):g<=T?(x=0,g=Math.min(Math.max(-u,-m),u),E=g*(g+2*m)+p):(x=Math.max(0,-(d*u+h)),g=x>0?u:Math.min(Math.max(-u,-m),u),E=-x*x+g*(g+2*m)+p);else g=d>0?-u:u,x=Math.max(0,-(d*g+h)),E=-x*x+g*(g+2*m)+p;return r&&r.copy(this.origin).addScaledVector(this.direction,x),l&&l.copy(Ld).addScaledVector(Tc,g),E}intersectSphere(e,i){va.subVectors(e.center,this.origin);const r=va.dot(this.direction),l=va.dot(va)-r*r,u=e.radius*e.radius;if(l>u)return null;const d=Math.sqrt(u-l),h=r-d,m=r+d;return m<0?null:h<0?this.at(m,i):this.at(h,i)}intersectsSphere(e){return e.radius<0?!1:this.distanceSqToPoint(e.center)<=e.radius*e.radius}distanceToPlane(e){const i=e.normal.dot(this.direction);if(i===0)return e.distanceToPoint(this.origin)===0?0:null;const r=-(this.origin.dot(e.normal)+e.constant)/i;return r>=0?r:null}intersectPlane(e,i){const r=this.distanceToPlane(e);return r===null?null:this.at(r,i)}intersectsPlane(e){const i=e.distanceToPoint(this.origin);return i===0||e.normal.dot(this.direction)*i<0}intersectBox(e,i){let r,l,u,d,h,m;const p=1/this.direction.x,v=1/this.direction.y,x=1/this.direction.z,g=this.origin;return p>=0?(r=(e.min.x-g.x)*p,l=(e.max.x-g.x)*p):(r=(e.max.x-g.x)*p,l=(e.min.x-g.x)*p),v>=0?(u=(e.min.y-g.y)*v,d=(e.max.y-g.y)*v):(u=(e.max.y-g.y)*v,d=(e.min.y-g.y)*v),r>d||u>l||((u>r||isNaN(r))&&(r=u),(d<l||isNaN(l))&&(l=d),x>=0?(h=(e.min.z-g.z)*x,m=(e.max.z-g.z)*x):(h=(e.max.z-g.z)*x,m=(e.min.z-g.z)*x),r>m||h>l)||((h>r||r!==r)&&(r=h),(m<l||l!==l)&&(l=m),l<0)?null:this.at(r>=0?r:l,i)}intersectsBox(e){return this.intersectBox(e,va)!==null}intersectTriangle(e,i,r,l,u){Nd.subVectors(i,e),Ac.subVectors(r,e),Od.crossVectors(Nd,Ac);let d=this.direction.dot(Od),h;if(d>0){if(l)return null;h=1}else if(d<0)h=-1,d=-d;else return null;as.subVectors(this.origin,e);const m=h*this.direction.dot(Ac.crossVectors(as,Ac));if(m<0)return null;const p=h*this.direction.dot(Nd.cross(as));if(p<0||m+p>d)return null;const v=-h*as.dot(Od);return v<0?null:this.at(v/d,u)}applyMatrix4(e){return this.origin.applyMatrix4(e),this.direction.transformDirection(e),this}equals(e){return e.origin.equals(this.origin)&&e.direction.equals(this.direction)}clone(){return new this.constructor().copy(this)}}class Qh extends tl{constructor(e){super(),this.isMeshBasicMaterial=!0,this.type="MeshBasicMaterial",this.color=new Et(16777215),this.map=null,this.lightMap=null,this.lightMapIntensity=1,this.aoMap=null,this.aoMapIntensity=1,this.specularMap=null,this.alphaMap=null,this.envMap=null,this.envMapRotation=new Gs,this.combine=nv,this.reflectivity=1,this.refractionRatio=.98,this.wireframe=!1,this.wireframeLinewidth=1,this.wireframeLinecap="round",this.wireframeLinejoin="round",this.fog=!0,this.setValues(e)}copy(e){return super.copy(e),this.color.copy(e.color),this.map=e.map,this.lightMap=e.lightMap,this.lightMapIntensity=e.lightMapIntensity,this.aoMap=e.aoMap,this.aoMapIntensity=e.aoMapIntensity,this.specularMap=e.specularMap,this.alphaMap=e.alphaMap,this.envMap=e.envMap,this.envMapRotation.copy(e.envMapRotation),this.combine=e.combine,this.reflectivity=e.reflectivity,this.refractionRatio=e.refractionRatio,this.wireframe=e.wireframe,this.wireframeLinewidth=e.wireframeLinewidth,this.wireframeLinecap=e.wireframeLinecap,this.wireframeLinejoin=e.wireframeLinejoin,this.fog=e.fog,this}}const p_=new fn,Ls=new bv,Rc=new tu,m_=new ae,Cc=new ae,wc=new ae,Dc=new ae,Pd=new ae,Uc=new ae,g_=new ae,Lc=new ae;class Ui extends Zn{constructor(e=new Li,i=new Qh){super(),this.isMesh=!0,this.type="Mesh",this.geometry=e,this.material=i,this.morphTargetDictionary=void 0,this.morphTargetInfluences=void 0,this.count=1,this.updateMorphTargets()}copy(e,i){return super.copy(e,i),e.morphTargetInfluences!==void 0&&(this.morphTargetInfluences=e.morphTargetInfluences.slice()),e.morphTargetDictionary!==void 0&&(this.morphTargetDictionary=Object.assign({},e.morphTargetDictionary)),this.material=Array.isArray(e.material)?e.material.slice():e.material,this.geometry=e.geometry,this}updateMorphTargets(){const i=this.geometry.morphAttributes,r=Object.keys(i);if(r.length>0){const l=i[r[0]];if(l!==void 0){this.morphTargetInfluences=[],this.morphTargetDictionary={};for(let u=0,d=l.length;u<d;u++){const h=l[u].name||String(u);this.morphTargetInfluences.push(0),this.morphTargetDictionary[h]=u}}}}getVertexPosition(e,i){const r=this.geometry,l=r.attributes.position,u=r.morphAttributes.position,d=r.morphTargetsRelative;i.fromBufferAttribute(l,e);const h=this.morphTargetInfluences;if(u&&h){Uc.set(0,0,0);for(let m=0,p=u.length;m<p;m++){const v=h[m],x=u[m];v!==0&&(Pd.fromBufferAttribute(x,e),d?Uc.addScaledVector(Pd,v):Uc.addScaledVector(Pd.sub(i),v))}i.add(Uc)}return i}raycast(e,i){const r=this.geometry,l=this.material,u=this.matrixWorld;l!==void 0&&(r.boundingSphere===null&&r.computeBoundingSphere(),Rc.copy(r.boundingSphere),Rc.applyMatrix4(u),Ls.copy(e.ray).recast(e.near),!(Rc.containsPoint(Ls.origin)===!1&&(Ls.intersectSphere(Rc,m_)===null||Ls.origin.distanceToSquared(m_)>(e.far-e.near)**2))&&(p_.copy(u).invert(),Ls.copy(e.ray).applyMatrix4(p_),!(r.boundingBox!==null&&Ls.intersectsBox(r.boundingBox)===!1)&&this._computeIntersections(e,i,Ls)))}_computeIntersections(e,i,r){let l;const u=this.geometry,d=this.material,h=u.index,m=u.attributes.position,p=u.attributes.uv,v=u.attributes.uv1,x=u.attributes.normal,g=u.groups,E=u.drawRange;if(h!==null)if(Array.isArray(d))for(let T=0,D=g.length;T<D;T++){const y=g[T],S=d[y.materialIndex],z=Math.max(y.start,E.start),F=Math.min(h.count,Math.min(y.start+y.count,E.start+E.count));for(let C=z,O=F;C<O;C+=3){const U=h.getX(C),B=h.getX(C+1),b=h.getX(C+2);l=Nc(this,S,e,r,p,v,x,U,B,b),l&&(l.faceIndex=Math.floor(C/3),l.face.materialIndex=y.materialIndex,i.push(l))}}else{const T=Math.max(0,E.start),D=Math.min(h.count,E.start+E.count);for(let y=T,S=D;y<S;y+=3){const z=h.getX(y),F=h.getX(y+1),C=h.getX(y+2);l=Nc(this,d,e,r,p,v,x,z,F,C),l&&(l.faceIndex=Math.floor(y/3),i.push(l))}}else if(m!==void 0)if(Array.isArray(d))for(let T=0,D=g.length;T<D;T++){const y=g[T],S=d[y.materialIndex],z=Math.max(y.start,E.start),F=Math.min(m.count,Math.min(y.start+y.count,E.start+E.count));for(let C=z,O=F;C<O;C+=3){const U=C,B=C+1,b=C+2;l=Nc(this,S,e,r,p,v,x,U,B,b),l&&(l.faceIndex=Math.floor(C/3),l.face.materialIndex=y.materialIndex,i.push(l))}}else{const T=Math.max(0,E.start),D=Math.min(m.count,E.start+E.count);for(let y=T,S=D;y<S;y+=3){const z=y,F=y+1,C=y+2;l=Nc(this,d,e,r,p,v,x,z,F,C),l&&(l.faceIndex=Math.floor(y/3),i.push(l))}}}}function pE(s,e,i,r,l,u,d,h){let m;if(e.side===Yn?m=r.intersectTriangle(d,u,l,!0,h):m=r.intersectTriangle(l,u,d,e.side===ls,h),m===null)return null;Lc.copy(h),Lc.applyMatrix4(s.matrixWorld);const p=i.ray.origin.distanceTo(Lc);return p<i.near||p>i.far?null:{distance:p,point:Lc.clone(),object:s}}function Nc(s,e,i,r,l,u,d,h,m,p){s.getVertexPosition(h,Cc),s.getVertexPosition(m,wc),s.getVertexPosition(p,Dc);const v=pE(s,e,i,r,Cc,wc,Dc,g_);if(v){const x=new ae;wi.getBarycoord(g_,Cc,wc,Dc,x),l&&(v.uv=wi.getInterpolatedAttribute(l,h,m,p,x,new Ut)),u&&(v.uv1=wi.getInterpolatedAttribute(u,h,m,p,x,new Ut)),d&&(v.normal=wi.getInterpolatedAttribute(d,h,m,p,x,new ae),v.normal.dot(r.direction)>0&&v.normal.multiplyScalar(-1));const g={a:h,b:m,c:p,normal:new ae,materialIndex:0};wi.getNormal(Cc,wc,Dc,g.normal),v.face=g,v.barycoord=x}return v}class mE extends Bn{constructor(e=null,i=1,r=1,l,u,d,h,m,p=Dn,v=Dn,x,g){super(null,d,h,m,p,v,l,u,x,g),this.isDataTexture=!0,this.image={data:e,width:i,height:r},this.generateMipmaps=!1,this.flipY=!1,this.unpackAlignment=1}}const zd=new ae,gE=new ae,_E=new st;class Os{constructor(e=new ae(1,0,0),i=0){this.isPlane=!0,this.normal=e,this.constant=i}set(e,i){return this.normal.copy(e),this.constant=i,this}setComponents(e,i,r,l){return this.normal.set(e,i,r),this.constant=l,this}setFromNormalAndCoplanarPoint(e,i){return this.normal.copy(e),this.constant=-i.dot(this.normal),this}setFromCoplanarPoints(e,i,r){const l=zd.subVectors(r,i).cross(gE.subVectors(e,i)).normalize();return this.setFromNormalAndCoplanarPoint(l,e),this}copy(e){return this.normal.copy(e.normal),this.constant=e.constant,this}normalize(){const e=1/this.normal.length();return this.normal.multiplyScalar(e),this.constant*=e,this}negate(){return this.constant*=-1,this.normal.negate(),this}distanceToPoint(e){return this.normal.dot(e)+this.constant}distanceToSphere(e){return this.distanceToPoint(e.center)-e.radius}projectPoint(e,i){return i.copy(e).addScaledVector(this.normal,-this.distanceToPoint(e))}intersectLine(e,i,r=!0){const l=e.delta(zd),u=this.normal.dot(l);if(u===0)return this.distanceToPoint(e.start)===0?i.copy(e.start):null;const d=-(e.start.dot(this.normal)+this.constant)/u;return r===!0&&(d<0||d>1)?null:i.copy(e.start).addScaledVector(l,d)}intersectsLine(e){const i=this.distanceToPoint(e.start),r=this.distanceToPoint(e.end);return i<0&&r>0||r<0&&i>0}intersectsBox(e){return e.intersectsPlane(this)}intersectsSphere(e){return e.intersectsPlane(this)}coplanarPoint(e){return e.copy(this.normal).multiplyScalar(-this.constant)}applyMatrix4(e,i){const r=i||_E.getNormalMatrix(e),l=this.coplanarPoint(zd).applyMatrix4(e),u=this.normal.applyMatrix3(r).normalize();return this.constant=-l.dot(u),this}translate(e){return this.constant-=e.dot(this.normal),this}equals(e){return e.normal.equals(this.normal)&&e.constant===this.constant}clone(){return new this.constructor().copy(this)}}const Ns=new tu,vE=new Ut(.5,.5),Oc=new ae;class Tv{constructor(e=new Os,i=new Os,r=new Os,l=new Os,u=new Os,d=new Os){this.planes=[e,i,r,l,u,d]}set(e,i,r,l,u,d){const h=this.planes;return h[0].copy(e),h[1].copy(i),h[2].copy(r),h[3].copy(l),h[4].copy(u),h[5].copy(d),this}copy(e){const i=this.planes;for(let r=0;r<6;r++)i[r].copy(e.planes[r]);return this}setFromProjectionMatrix(e,i=ki,r=!1){const l=this.planes,u=e.elements,d=u[0],h=u[1],m=u[2],p=u[3],v=u[4],x=u[5],g=u[6],E=u[7],T=u[8],D=u[9],y=u[10],S=u[11],z=u[12],F=u[13],C=u[14],O=u[15];if(l[0].setComponents(p-d,E-v,S-T,O-z).normalize(),l[1].setComponents(p+d,E+v,S+T,O+z).normalize(),l[2].setComponents(p+h,E+x,S+D,O+F).normalize(),l[3].setComponents(p-h,E-x,S-D,O-F).normalize(),r)l[4].setComponents(m,g,y,C).normalize(),l[5].setComponents(p-m,E-g,S-y,O-C).normalize();else if(l[4].setComponents(p-m,E-g,S-y,O-C).normalize(),i===ki)l[5].setComponents(p+m,E+g,S+y,O+C).normalize();else if(i===Jc)l[5].setComponents(m,g,y,C).normalize();else throw new Error("THREE.Frustum.setFromProjectionMatrix(): Invalid coordinate system: "+i);return this}intersectsObject(e){if(e.boundingSphere!==void 0)e.boundingSphere===null&&e.computeBoundingSphere(),Ns.copy(e.boundingSphere).applyMatrix4(e.matrixWorld);else{const i=e.geometry;i.boundingSphere===null&&i.computeBoundingSphere(),Ns.copy(i.boundingSphere).applyMatrix4(e.matrixWorld)}return this.intersectsSphere(Ns)}intersectsSprite(e){Ns.center.set(0,0,0);const i=vE.distanceTo(e.center);return Ns.radius=.7071067811865476+i,Ns.applyMatrix4(e.matrixWorld),this.intersectsSphere(Ns)}intersectsSphere(e){const i=this.planes,r=e.center,l=-e.radius;for(let u=0;u<6;u++)if(i[u].distanceToPoint(r)<l)return!1;return!0}intersectsBox(e){const i=this.planes;for(let r=0;r<6;r++){const l=i[r];if(Oc.x=l.normal.x>0?e.max.x:e.min.x,Oc.y=l.normal.y>0?e.max.y:e.min.y,Oc.z=l.normal.z>0?e.max.z:e.min.z,l.distanceToPoint(Oc)<0)return!1}return!0}containsPoint(e){const i=this.planes;for(let r=0;r<6;r++)if(i[r].distanceToPoint(e)<0)return!1;return!0}clone(){return new this.constructor().copy(this)}}class Av extends tl{constructor(e){super(),this.isPointsMaterial=!0,this.type="PointsMaterial",this.color=new Et(16777215),this.map=null,this.alphaMap=null,this.size=1,this.sizeAttenuation=!0,this.fog=!0,this.setValues(e)}copy(e){return super.copy(e),this.color.copy(e.color),this.map=e.map,this.alphaMap=e.alphaMap,this.size=e.size,this.sizeAttenuation=e.sizeAttenuation,this.fog=e.fog,this}}const __=new fn,Ph=new bv,Pc=new tu,zc=new ae;class xE extends Zn{constructor(e=new Li,i=new Av){super(),this.isPoints=!0,this.type="Points",this.geometry=e,this.material=i,this.morphTargetDictionary=void 0,this.morphTargetInfluences=void 0,this.updateMorphTargets()}copy(e,i){return super.copy(e,i),this.material=Array.isArray(e.material)?e.material.slice():e.material,this.geometry=e.geometry,this}raycast(e,i){const r=this.geometry,l=this.matrixWorld,u=e.params.Points.threshold,d=r.drawRange;if(r.boundingSphere===null&&r.computeBoundingSphere(),Pc.copy(r.boundingSphere),Pc.applyMatrix4(l),Pc.radius+=u,e.ray.intersectsSphere(Pc)===!1)return;__.copy(l).invert(),Ph.copy(e.ray).applyMatrix4(__);const h=u/((this.scale.x+this.scale.y+this.scale.z)/3),m=h*h,p=r.index,x=r.attributes.position;if(p!==null){const g=Math.max(0,d.start),E=Math.min(p.count,d.start+d.count);for(let T=g,D=E;T<D;T++){const y=p.getX(T);zc.fromBufferAttribute(x,y),v_(zc,y,m,l,e,i,this)}}else{const g=Math.max(0,d.start),E=Math.min(x.count,d.start+d.count);for(let T=g,D=E;T<D;T++)zc.fromBufferAttribute(x,T),v_(zc,T,m,l,e,i,this)}}updateMorphTargets(){const i=this.geometry.morphAttributes,r=Object.keys(i);if(r.length>0){const l=i[r[0]];if(l!==void 0){this.morphTargetInfluences=[],this.morphTargetDictionary={};for(let u=0,d=l.length;u<d;u++){const h=l[u].name||String(u);this.morphTargetInfluences.push(0),this.morphTargetDictionary[h]=u}}}}}function v_(s,e,i,r,l,u,d){const h=Ph.distanceSqToPoint(s);if(h<i){const m=new ae;Ph.closestPointToPoint(s,m),m.applyMatrix4(r);const p=l.ray.origin.distanceTo(m);if(p<l.near||p>l.far)return;u.push({distance:p,distanceToRay:Math.sqrt(h),point:m,index:e,face:null,faceIndex:null,barycoord:null,object:d})}}class Rv extends Bn{constructor(e=[],i=Is,r,l,u,d,h,m,p,v){super(e,i,r,l,u,d,h,m,p,v),this.isCubeTexture=!0,this.flipY=!1}get images(){return this.image}set images(e){this.image=e}}class Vr extends Bn{constructor(e,i,r=qi,l,u,d,h=Dn,m=Dn,p,v=Ta,x=1){if(v!==Ta&&v!==Bs)throw new Error("THREE.DepthTexture: format must be either THREE.DepthFormat or THREE.DepthStencilFormat");const g={width:e,height:i,depth:x};super(g,l,u,d,h,m,v,r,p),this.isDepthTexture=!0,this.flipY=!1,this.generateMipmaps=!1,this.compareFunction=null}copy(e){return super.copy(e),this.source=new Zh(Object.assign({},e.image)),this.compareFunction=e.compareFunction,this}toJSON(e){const i=super.toJSON(e);return this.compareFunction!==null&&(i.compareFunction=this.compareFunction),i}}class SE extends Vr{constructor(e,i=qi,r=Is,l,u,d=Dn,h=Dn,m,p=Ta){const v={width:e,height:e,depth:1},x=[v,v,v,v,v,v];super(e,e,i,r,l,u,d,h,m,p),this.image=x,this.isCubeDepthTexture=!0,this.isCubeTexture=!0}get images(){return this.image}set images(e){this.image=e}}class Cv extends Bn{constructor(e=null){super(),this.sourceTexture=e,this.isExternalTexture=!0}copy(e){return super.copy(e),this.sourceTexture=e.sourceTexture,this}}class nl extends Li{constructor(e=1,i=1,r=1,l=1,u=1,d=1){super(),this.type="BoxGeometry",this.parameters={width:e,height:i,depth:r,widthSegments:l,heightSegments:u,depthSegments:d};const h=this;l=Math.floor(l),u=Math.floor(u),d=Math.floor(d);const m=[],p=[],v=[],x=[];let g=0,E=0;T("z","y","x",-1,-1,r,i,e,d,u,0),T("z","y","x",1,-1,r,i,-e,d,u,1),T("x","z","y",1,1,e,r,i,l,d,2),T("x","z","y",1,-1,e,r,-i,l,d,3),T("x","y","z",1,-1,e,i,r,l,u,4),T("x","y","z",-1,-1,e,i,-r,l,u,5),this.setIndex(m),this.setAttribute("position",new Ea(p,3)),this.setAttribute("normal",new Ea(v,3)),this.setAttribute("uv",new Ea(x,2));function T(D,y,S,z,F,C,O,U,B,b,L){const W=C/B,V=O/b,Z=C/2,de=O/2,he=U/2,K=B+1,N=b+1;let I=0,$=0;const se=new ae;for(let pe=0;pe<N;pe++){const xe=pe*V-de;for(let Se=0;Se<K;Se++){const Te=Se*W-Z;se[D]=Te*z,se[y]=xe*F,se[S]=he,p.push(se.x,se.y,se.z),se[D]=0,se[y]=0,se[S]=U>0?1:-1,v.push(se.x,se.y,se.z),x.push(Se/B),x.push(1-pe/b),I+=1}}for(let pe=0;pe<b;pe++)for(let xe=0;xe<B;xe++){const Se=g+xe+K*pe,Te=g+xe+K*(pe+1),tt=g+(xe+1)+K*(pe+1),nt=g+(xe+1)+K*pe;m.push(Se,Te,nt),m.push(Te,tt,nt),$+=6}h.addGroup(E,$,L),E+=$,g+=I}}copy(e){return super.copy(e),this.parameters=Object.assign({},e.parameters),this}static fromJSON(e){return new nl(e.width,e.height,e.depth,e.widthSegments,e.heightSegments,e.depthSegments)}}class il extends Li{constructor(e=1,i=1,r=1,l=1){super(),this.type="PlaneGeometry",this.parameters={width:e,height:i,widthSegments:r,heightSegments:l};const u=e/2,d=i/2,h=Math.floor(r),m=Math.floor(l),p=h+1,v=m+1,x=e/h,g=i/m,E=[],T=[],D=[],y=[];for(let S=0;S<v;S++){const z=S*g-d;for(let F=0;F<p;F++){const C=F*x-u;T.push(C,-z,0),D.push(0,0,1),y.push(F/h),y.push(1-S/m)}}for(let S=0;S<m;S++)for(let z=0;z<h;z++){const F=z+p*S,C=z+p*(S+1),O=z+1+p*(S+1),U=z+1+p*S;E.push(F,C,U),E.push(C,O,U)}this.setIndex(E),this.setAttribute("position",new Ea(T,3)),this.setAttribute("normal",new Ea(D,3)),this.setAttribute("uv",new Ea(y,2))}copy(e){return super.copy(e),this.parameters=Object.assign({},e.parameters),this}static fromJSON(e){return new il(e.width,e.height,e.widthSegments,e.heightSegments)}}function kr(s){const e={};for(const i in s){e[i]={};for(const r in s[i]){const l=s[i][r];if(x_(l))l.isRenderTargetTexture?(et("UniformsUtils: Textures of render targets cannot be cloned via cloneUniforms() or mergeUniforms()."),e[i][r]=null):e[i][r]=l.clone();else if(Array.isArray(l))if(x_(l[0])){const u=[];for(let d=0,h=l.length;d<h;d++)u[d]=l[d].clone();e[i][r]=u}else e[i][r]=l.slice();else e[i][r]=l}}return e}function Fn(s){const e={};for(let i=0;i<s.length;i++){const r=kr(s[i]);for(const l in r)e[l]=r[l]}return e}function x_(s){return s&&(s.isColor||s.isMatrix3||s.isMatrix4||s.isVector2||s.isVector3||s.isVector4||s.isTexture||s.isQuaternion)}function ME(s){const e=[];for(let i=0;i<s.length;i++)e.push(s[i].clone());return e}function wv(s){const e=s.getRenderTarget();return e===null?s.outputColorSpace:e.isXRRenderTarget===!0?e.texture.colorSpace:yt.workingColorSpace}const yE={clone:kr,merge:Fn};var EE=`void main() {
	gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );
}`,bE=`void main() {
	gl_FragColor = vec4( 1.0, 0.0, 0.0, 1.0 );
}`;class Yi extends tl{constructor(e){super(),this.isShaderMaterial=!0,this.type="ShaderMaterial",this.defines={},this.uniforms={},this.uniformsGroups=[],this.vertexShader=EE,this.fragmentShader=bE,this.linewidth=1,this.wireframe=!1,this.wireframeLinewidth=1,this.fog=!1,this.lights=!1,this.clipping=!1,this.forceSinglePass=!0,this.extensions={clipCullDistance:!1,multiDraw:!1},this.defaultAttributeValues={color:[1,1,1],uv:[0,0],uv1:[0,0]},this.index0AttributeName=void 0,this.uniformsNeedUpdate=!1,this.glslVersion=null,e!==void 0&&this.setValues(e)}copy(e){return super.copy(e),this.fragmentShader=e.fragmentShader,this.vertexShader=e.vertexShader,this.uniforms=kr(e.uniforms),this.uniformsGroups=ME(e.uniformsGroups),this.defines=Object.assign({},e.defines),this.wireframe=e.wireframe,this.wireframeLinewidth=e.wireframeLinewidth,this.fog=e.fog,this.lights=e.lights,this.clipping=e.clipping,this.extensions=Object.assign({},e.extensions),this.glslVersion=e.glslVersion,this.defaultAttributeValues=Object.assign({},e.defaultAttributeValues),this.index0AttributeName=e.index0AttributeName,this.uniformsNeedUpdate=e.uniformsNeedUpdate,this}toJSON(e){const i=super.toJSON(e);i.glslVersion=this.glslVersion,i.uniforms={};for(const l in this.uniforms){const d=this.uniforms[l].value;d&&d.isTexture?i.uniforms[l]={type:"t",value:d.toJSON(e).uuid}:d&&d.isColor?i.uniforms[l]={type:"c",value:d.getHex()}:d&&d.isVector2?i.uniforms[l]={type:"v2",value:d.toArray()}:d&&d.isVector3?i.uniforms[l]={type:"v3",value:d.toArray()}:d&&d.isVector4?i.uniforms[l]={type:"v4",value:d.toArray()}:d&&d.isMatrix3?i.uniforms[l]={type:"m3",value:d.toArray()}:d&&d.isMatrix4?i.uniforms[l]={type:"m4",value:d.toArray()}:i.uniforms[l]={value:d}}Object.keys(this.defines).length>0&&(i.defines=this.defines),i.vertexShader=this.vertexShader,i.fragmentShader=this.fragmentShader,i.lights=this.lights,i.clipping=this.clipping;const r={};for(const l in this.extensions)this.extensions[l]===!0&&(r[l]=!0);return Object.keys(r).length>0&&(i.extensions=r),i}fromJSON(e,i){if(super.fromJSON(e,i),e.uniforms!==void 0)for(const r in e.uniforms){const l=e.uniforms[r];switch(this.uniforms[r]={},l.type){case"t":this.uniforms[r].value=i[l.value]||null;break;case"c":this.uniforms[r].value=new Et().setHex(l.value);break;case"v2":this.uniforms[r].value=new Ut().fromArray(l.value);break;case"v3":this.uniforms[r].value=new ae().fromArray(l.value);break;case"v4":this.uniforms[r].value=new on().fromArray(l.value);break;case"m3":this.uniforms[r].value=new st().fromArray(l.value);break;case"m4":this.uniforms[r].value=new fn().fromArray(l.value);break;default:this.uniforms[r].value=l.value}}if(e.defines!==void 0&&(this.defines=e.defines),e.vertexShader!==void 0&&(this.vertexShader=e.vertexShader),e.fragmentShader!==void 0&&(this.fragmentShader=e.fragmentShader),e.glslVersion!==void 0&&(this.glslVersion=e.glslVersion),e.extensions!==void 0)for(const r in e.extensions)this.extensions[r]=e.extensions[r];return e.lights!==void 0&&(this.lights=e.lights),e.clipping!==void 0&&(this.clipping=e.clipping),this}}class TE extends Yi{constructor(e){super(e),this.isRawShaderMaterial=!0,this.type="RawShaderMaterial"}}class AE extends tl{constructor(e){super(),this.isMeshDepthMaterial=!0,this.type="MeshDepthMaterial",this.depthPacking=yy,this.map=null,this.alphaMap=null,this.displacementMap=null,this.displacementScale=1,this.displacementBias=0,this.wireframe=!1,this.wireframeLinewidth=1,this.setValues(e)}copy(e){return super.copy(e),this.depthPacking=e.depthPacking,this.map=e.map,this.alphaMap=e.alphaMap,this.displacementMap=e.displacementMap,this.displacementScale=e.displacementScale,this.displacementBias=e.displacementBias,this.wireframe=e.wireframe,this.wireframeLinewidth=e.wireframeLinewidth,this}}class RE extends tl{constructor(e){super(),this.isMeshDistanceMaterial=!0,this.type="MeshDistanceMaterial",this.map=null,this.alphaMap=null,this.displacementMap=null,this.displacementScale=1,this.displacementBias=0,this.setValues(e)}copy(e){return super.copy(e),this.map=e.map,this.alphaMap=e.alphaMap,this.displacementMap=e.displacementMap,this.displacementScale=e.displacementScale,this.displacementBias=e.displacementBias,this}}const Fc=new ae,Bc=new Wr,Ii=new ae;class Dv extends Zn{constructor(){super(),this.isCamera=!0,this.type="Camera",this.matrixWorldInverse=new fn,this.projectionMatrix=new fn,this.projectionMatrixInverse=new fn,this.coordinateSystem=ki,this._reversedDepth=!1}get reversedDepth(){return this._reversedDepth}copy(e,i){return super.copy(e,i),this.matrixWorldInverse.copy(e.matrixWorldInverse),this.projectionMatrix.copy(e.projectionMatrix),this.projectionMatrixInverse.copy(e.projectionMatrixInverse),this.coordinateSystem=e.coordinateSystem,this}getWorldDirection(e){return super.getWorldDirection(e).negate()}updateMatrixWorld(e){super.updateMatrixWorld(e),this.matrixWorld.decompose(Fc,Bc,Ii),Ii.x===1&&Ii.y===1&&Ii.z===1?this.matrixWorldInverse.copy(this.matrixWorld).invert():this.matrixWorldInverse.compose(Fc,Bc,Ii.set(1,1,1)).invert()}updateWorldMatrix(e,i,r=!1){super.updateWorldMatrix(e,i,r),this.matrixWorld.decompose(Fc,Bc,Ii),Ii.x===1&&Ii.y===1&&Ii.z===1?this.matrixWorldInverse.copy(this.matrixWorld).invert():this.matrixWorldInverse.compose(Fc,Bc,Ii.set(1,1,1)).invert()}clone(){return new this.constructor().copy(this)}}const ss=new ae,S_=new Ut,M_=new Ut;class vi extends Dv{constructor(e=50,i=1,r=.1,l=2e3){super(),this.isPerspectiveCamera=!0,this.type="PerspectiveCamera",this.fov=e,this.zoom=1,this.near=r,this.far=l,this.focus=10,this.aspect=i,this.view=null,this.filmGauge=35,this.filmOffset=0,this.updateProjectionMatrix()}copy(e,i){return super.copy(e,i),this.fov=e.fov,this.zoom=e.zoom,this.near=e.near,this.far=e.far,this.focus=e.focus,this.aspect=e.aspect,this.view=e.view===null?null:Object.assign({},e.view),this.filmGauge=e.filmGauge,this.filmOffset=e.filmOffset,this}setFocalLength(e){const i=.5*this.getFilmHeight()/e;this.fov=jo*2*Math.atan(i),this.updateProjectionMatrix()}getFocalLength(){const e=Math.tan(Ko*.5*this.fov);return .5*this.getFilmHeight()/e}getEffectiveFOV(){return jo*2*Math.atan(Math.tan(Ko*.5*this.fov)/this.zoom)}getFilmWidth(){return this.filmGauge*Math.min(this.aspect,1)}getFilmHeight(){return this.filmGauge/Math.max(this.aspect,1)}getViewBounds(e,i,r){ss.set(-1,-1,.5).applyMatrix4(this.projectionMatrixInverse),i.set(ss.x,ss.y).multiplyScalar(-e/ss.z),ss.set(1,1,.5).applyMatrix4(this.projectionMatrixInverse),r.set(ss.x,ss.y).multiplyScalar(-e/ss.z)}getViewSize(e,i){return this.getViewBounds(e,S_,M_),i.subVectors(M_,S_)}setViewOffset(e,i,r,l,u,d){this.aspect=e/i,this.view===null&&(this.view={enabled:!0,fullWidth:1,fullHeight:1,offsetX:0,offsetY:0,width:1,height:1}),this.view.enabled=!0,this.view.fullWidth=e,this.view.fullHeight=i,this.view.offsetX=r,this.view.offsetY=l,this.view.width=u,this.view.height=d,this.updateProjectionMatrix()}clearViewOffset(){this.view!==null&&(this.view.enabled=!1),this.updateProjectionMatrix()}updateProjectionMatrix(){const e=this.near;let i=e*Math.tan(Ko*.5*this.fov)/this.zoom,r=2*i,l=this.aspect*r,u=-.5*l;const d=this.view;if(this.view!==null&&this.view.enabled){const m=d.fullWidth,p=d.fullHeight;u+=d.offsetX*l/m,i-=d.offsetY*r/p,l*=d.width/m,r*=d.height/p}const h=this.filmOffset;h!==0&&(u+=e*h/this.getFilmWidth()),this.projectionMatrix.makePerspective(u,u+l,i,i-r,e,this.far,this.coordinateSystem,this.reversedDepth),this.projectionMatrixInverse.copy(this.projectionMatrix).invert()}toJSON(e){const i=super.toJSON(e);return i.object.fov=this.fov,i.object.zoom=this.zoom,i.object.near=this.near,i.object.far=this.far,i.object.focus=this.focus,i.object.aspect=this.aspect,this.view!==null&&(i.object.view=Object.assign({},this.view)),i.object.filmGauge=this.filmGauge,i.object.filmOffset=this.filmOffset,i}}class Uv extends Dv{constructor(e=-1,i=1,r=1,l=-1,u=.1,d=2e3){super(),this.isOrthographicCamera=!0,this.type="OrthographicCamera",this.zoom=1,this.view=null,this.left=e,this.right=i,this.top=r,this.bottom=l,this.near=u,this.far=d,this.updateProjectionMatrix()}copy(e,i){return super.copy(e,i),this.left=e.left,this.right=e.right,this.top=e.top,this.bottom=e.bottom,this.near=e.near,this.far=e.far,this.zoom=e.zoom,this.view=e.view===null?null:Object.assign({},e.view),this}setViewOffset(e,i,r,l,u,d){this.view===null&&(this.view={enabled:!0,fullWidth:1,fullHeight:1,offsetX:0,offsetY:0,width:1,height:1}),this.view.enabled=!0,this.view.fullWidth=e,this.view.fullHeight=i,this.view.offsetX=r,this.view.offsetY=l,this.view.width=u,this.view.height=d,this.updateProjectionMatrix()}clearViewOffset(){this.view!==null&&(this.view.enabled=!1),this.updateProjectionMatrix()}updateProjectionMatrix(){const e=(this.right-this.left)/(2*this.zoom),i=(this.top-this.bottom)/(2*this.zoom),r=(this.right+this.left)/2,l=(this.top+this.bottom)/2;let u=r-e,d=r+e,h=l+i,m=l-i;if(this.view!==null&&this.view.enabled){const p=(this.right-this.left)/this.view.fullWidth/this.zoom,v=(this.top-this.bottom)/this.view.fullHeight/this.zoom;u+=p*this.view.offsetX,d=u+p*this.view.width,h-=v*this.view.offsetY,m=h-v*this.view.height}this.projectionMatrix.makeOrthographic(u,d,h,m,this.near,this.far,this.coordinateSystem,this.reversedDepth),this.projectionMatrixInverse.copy(this.projectionMatrix).invert()}toJSON(e){const i=super.toJSON(e);return i.object.zoom=this.zoom,i.object.left=this.left,i.object.right=this.right,i.object.top=this.top,i.object.bottom=this.bottom,i.object.near=this.near,i.object.far=this.far,this.view!==null&&(i.object.view=Object.assign({},this.view)),i}}const Lr=-90,Nr=1;class CE extends Zn{constructor(e,i,r){super(),this.type="CubeCamera",this.renderTarget=r,this.coordinateSystem=null,this.activeMipmapLevel=0;const l=new vi(Lr,Nr,e,i);l.layers=this.layers,this.add(l);const u=new vi(Lr,Nr,e,i);u.layers=this.layers,this.add(u);const d=new vi(Lr,Nr,e,i);d.layers=this.layers,this.add(d);const h=new vi(Lr,Nr,e,i);h.layers=this.layers,this.add(h);const m=new vi(Lr,Nr,e,i);m.layers=this.layers,this.add(m);const p=new vi(Lr,Nr,e,i);p.layers=this.layers,this.add(p)}updateCoordinateSystem(){const e=this.coordinateSystem,i=this.children.concat(),[r,l,u,d,h,m]=i;for(const p of i)this.remove(p);if(e===ki)r.up.set(0,1,0),r.lookAt(1,0,0),l.up.set(0,1,0),l.lookAt(-1,0,0),u.up.set(0,0,-1),u.lookAt(0,1,0),d.up.set(0,0,1),d.lookAt(0,-1,0),h.up.set(0,1,0),h.lookAt(0,0,1),m.up.set(0,1,0),m.lookAt(0,0,-1);else if(e===Jc)r.up.set(0,-1,0),r.lookAt(-1,0,0),l.up.set(0,-1,0),l.lookAt(1,0,0),u.up.set(0,0,1),u.lookAt(0,1,0),d.up.set(0,0,-1),d.lookAt(0,-1,0),h.up.set(0,-1,0),h.lookAt(0,0,1),m.up.set(0,-1,0),m.lookAt(0,0,-1);else throw new Error("THREE.CubeCamera.updateCoordinateSystem(): Invalid coordinate system: "+e);for(const p of i)this.add(p),p.updateMatrixWorld()}update(e,i){this.parent===null&&this.updateMatrixWorld();const{renderTarget:r,activeMipmapLevel:l}=this;this.coordinateSystem!==e.coordinateSystem&&(this.coordinateSystem=e.coordinateSystem,this.updateCoordinateSystem());const[u,d,h,m,p,v]=this.children,x=e.getRenderTarget(),g=e.getActiveCubeFace(),E=e.getActiveMipmapLevel(),T=e.xr.enabled;e.xr.enabled=!1;const D=r.texture.generateMipmaps;r.texture.generateMipmaps=!1;let y=!1;e.isWebGLRenderer===!0?y=e.state.buffers.depth.getReversed():y=e.reversedDepthBuffer,e.setRenderTarget(r,0,l),y&&e.autoClear===!1&&e.clearDepth(),e.render(i,u),e.setRenderTarget(r,1,l),y&&e.autoClear===!1&&e.clearDepth(),e.render(i,d),e.setRenderTarget(r,2,l),y&&e.autoClear===!1&&e.clearDepth(),e.render(i,h),e.setRenderTarget(r,3,l),y&&e.autoClear===!1&&e.clearDepth(),e.render(i,m),e.setRenderTarget(r,4,l),y&&e.autoClear===!1&&e.clearDepth(),e.render(i,p),r.texture.generateMipmaps=D,e.setRenderTarget(r,5,l),y&&e.autoClear===!1&&e.clearDepth(),e.render(i,v),e.setRenderTarget(x,g,E),e.xr.enabled=T,r.texture.needsPMREMUpdate=!0}}class wE extends vi{constructor(e=[]){super(),this.isArrayCamera=!0,this.isMultiViewCamera=!1,this.cameras=e}}class DE{constructor(e=!0){this.autoStart=e,this.startTime=0,this.oldTime=0,this.elapsedTime=0,this.running=!1,et("Clock: This module has been deprecated. Please use THREE.Timer instead.")}start(){this.startTime=performance.now(),this.oldTime=this.startTime,this.elapsedTime=0,this.running=!0}stop(){this.getElapsedTime(),this.running=!1,this.autoStart=!1}getElapsedTime(){return this.getDelta(),this.elapsedTime}getDelta(){let e=0;if(this.autoStart&&!this.running)return this.start(),0;if(this.running){const i=performance.now();e=(i-this.oldTime)/1e3,this.oldTime=i,this.elapsedTime+=e}return e}}const tp=class tp{constructor(e,i,r,l){this.elements=[1,0,0,1],e!==void 0&&this.set(e,i,r,l)}identity(){return this.set(1,0,0,1),this}fromArray(e,i=0){for(let r=0;r<4;r++)this.elements[r]=e[r+i];return this}set(e,i,r,l){const u=this.elements;return u[0]=e,u[2]=i,u[1]=r,u[3]=l,this}};tp.prototype.isMatrix2=!0;let y_=tp;function E_(s,e,i,r){const l=UE(r);switch(i){case mv:return s*e;case _v:return s*e/l.components*l.byteLength;case Vh:return s*e/l.components*l.byteLength;case Hs:return s*e*2/l.components*l.byteLength;case kh:return s*e*2/l.components*l.byteLength;case gv:return s*e*3/l.components*l.byteLength;case Di:return s*e*4/l.components*l.byteLength;case Xh:return s*e*4/l.components*l.byteLength;case Vc:case kc:return Math.floor((s+3)/4)*Math.floor((e+3)/4)*8;case Xc:case Wc:return Math.floor((s+3)/4)*Math.floor((e+3)/4)*16;case rh:case lh:return Math.max(s,16)*Math.max(e,8)/4;case sh:case oh:return Math.max(s,8)*Math.max(e,8)/2;case ch:case uh:case dh:case hh:return Math.floor((s+3)/4)*Math.floor((e+3)/4)*8;case fh:case Yc:case ph:return Math.floor((s+3)/4)*Math.floor((e+3)/4)*16;case mh:return Math.floor((s+3)/4)*Math.floor((e+3)/4)*16;case gh:return Math.floor((s+4)/5)*Math.floor((e+3)/4)*16;case _h:return Math.floor((s+4)/5)*Math.floor((e+4)/5)*16;case vh:return Math.floor((s+5)/6)*Math.floor((e+4)/5)*16;case xh:return Math.floor((s+5)/6)*Math.floor((e+5)/6)*16;case Sh:return Math.floor((s+7)/8)*Math.floor((e+4)/5)*16;case Mh:return Math.floor((s+7)/8)*Math.floor((e+5)/6)*16;case yh:return Math.floor((s+7)/8)*Math.floor((e+7)/8)*16;case Eh:return Math.floor((s+9)/10)*Math.floor((e+4)/5)*16;case bh:return Math.floor((s+9)/10)*Math.floor((e+5)/6)*16;case Th:return Math.floor((s+9)/10)*Math.floor((e+7)/8)*16;case Ah:return Math.floor((s+9)/10)*Math.floor((e+9)/10)*16;case Rh:return Math.floor((s+11)/12)*Math.floor((e+9)/10)*16;case Ch:return Math.floor((s+11)/12)*Math.floor((e+11)/12)*16;case wh:case Dh:case Uh:return Math.ceil(s/4)*Math.ceil(e/4)*16;case Lh:case Nh:return Math.ceil(s/4)*Math.ceil(e/4)*8;case Zc:case Oh:return Math.ceil(s/4)*Math.ceil(e/4)*16}throw new Error(`Unable to determine texture byte length for ${i} format.`)}function UE(s){switch(s){case xi:case fv:return{byteLength:1,components:1};case Jo:case dv:case ba:return{byteLength:2,components:1};case Hh:case Gh:return{byteLength:2,components:4};case qi:case Ih:case Vi:return{byteLength:4,components:1};case hv:case pv:return{byteLength:4,components:3}}throw new Error(`THREE.TextureUtils: Unknown texture type ${s}.`)}typeof __THREE_DEVTOOLS__<"u"&&__THREE_DEVTOOLS__.dispatchEvent(new CustomEvent("register",{detail:{revision:Bh}}));typeof window<"u"&&(window.__THREE__?et("WARNING: Multiple instances of Three.js being imported."):window.__THREE__=Bh);/**
 * @license
 * Copyright 2010-2026 Three.js Authors
 * SPDX-License-Identifier: MIT
 */function Lv(){let s=null,e=!1,i=null,r=null;function l(u,d){i(u,d),r=s.requestAnimationFrame(l)}return{start:function(){e!==!0&&i!==null&&s!==null&&(r=s.requestAnimationFrame(l),e=!0)},stop:function(){s!==null&&s.cancelAnimationFrame(r),e=!1},setAnimationLoop:function(u){i=u},setContext:function(u){s=u}}}function LE(s){const e=new WeakMap;function i(h,m){const p=h.array,v=h.usage,x=p.byteLength,g=s.createBuffer();s.bindBuffer(m,g),s.bufferData(m,p,v),h.onUploadCallback();let E;if(p instanceof Float32Array)E=s.FLOAT;else if(typeof Float16Array<"u"&&p instanceof Float16Array)E=s.HALF_FLOAT;else if(p instanceof Uint16Array)h.isFloat16BufferAttribute?E=s.HALF_FLOAT:E=s.UNSIGNED_SHORT;else if(p instanceof Int16Array)E=s.SHORT;else if(p instanceof Uint32Array)E=s.UNSIGNED_INT;else if(p instanceof Int32Array)E=s.INT;else if(p instanceof Int8Array)E=s.BYTE;else if(p instanceof Uint8Array)E=s.UNSIGNED_BYTE;else if(p instanceof Uint8ClampedArray)E=s.UNSIGNED_BYTE;else throw new Error("THREE.WebGLAttributes: Unsupported buffer data format: "+p);return{buffer:g,type:E,bytesPerElement:p.BYTES_PER_ELEMENT,version:h.version,size:x}}function r(h,m,p){const v=m.array,x=m.updateRanges;if(s.bindBuffer(p,h),x.length===0)s.bufferSubData(p,0,v);else{x.sort((E,T)=>E.start-T.start);let g=0;for(let E=1;E<x.length;E++){const T=x[g],D=x[E];D.start<=T.start+T.count+1?T.count=Math.max(T.count,D.start+D.count-T.start):(++g,x[g]=D)}x.length=g+1;for(let E=0,T=x.length;E<T;E++){const D=x[E];s.bufferSubData(p,D.start*v.BYTES_PER_ELEMENT,v,D.start,D.count)}m.clearUpdateRanges()}m.onUploadCallback()}function l(h){return h.isInterleavedBufferAttribute&&(h=h.data),e.get(h)}function u(h){h.isInterleavedBufferAttribute&&(h=h.data);const m=e.get(h);m&&(s.deleteBuffer(m.buffer),e.delete(h))}function d(h,m){if(h.isInterleavedBufferAttribute&&(h=h.data),h.isGLBufferAttribute){const v=e.get(h);(!v||v.version<h.version)&&e.set(h,{buffer:h.buffer,type:h.type,bytesPerElement:h.elementSize,version:h.version});return}const p=e.get(h);if(p===void 0)e.set(h,i(h,m));else if(p.version<h.version){if(p.size!==h.array.byteLength)throw new Error("THREE.WebGLAttributes: The size of the buffer attribute's array buffer does not match the original size. Resizing buffer attributes is not supported.");r(p.buffer,h,m),p.version=h.version}}return{get:l,remove:u,update:d}}var NE=`#ifdef USE_ALPHAHASH
	if ( diffuseColor.a < getAlphaHashThreshold( vPosition ) ) discard;
#endif`,OE=`#ifdef USE_ALPHAHASH
	const float ALPHA_HASH_SCALE = 0.05;
	float hash2D( vec2 value ) {
		return fract( 1.0e4 * sin( 17.0 * value.x + 0.1 * value.y ) * ( 0.1 + abs( sin( 13.0 * value.y + value.x ) ) ) );
	}
	float hash3D( vec3 value ) {
		return hash2D( vec2( hash2D( value.xy ), value.z ) );
	}
	float getAlphaHashThreshold( vec3 position ) {
		float maxDeriv = max(
			length( dFdx( position.xyz ) ),
			length( dFdy( position.xyz ) )
		);
		float pixScale = 1.0 / ( ALPHA_HASH_SCALE * maxDeriv );
		vec2 pixScales = vec2(
			exp2( floor( log2( pixScale ) ) ),
			exp2( ceil( log2( pixScale ) ) )
		);
		vec2 alpha = vec2(
			hash3D( floor( pixScales.x * position.xyz ) ),
			hash3D( floor( pixScales.y * position.xyz ) )
		);
		float lerpFactor = fract( log2( pixScale ) );
		float x = ( 1.0 - lerpFactor ) * alpha.x + lerpFactor * alpha.y;
		float a = min( lerpFactor, 1.0 - lerpFactor );
		vec3 cases = vec3(
			x * x / ( 2.0 * a * ( 1.0 - a ) ),
			( x - 0.5 * a ) / ( 1.0 - a ),
			1.0 - ( ( 1.0 - x ) * ( 1.0 - x ) / ( 2.0 * a * ( 1.0 - a ) ) )
		);
		float threshold = ( x < ( 1.0 - a ) )
			? ( ( x < a ) ? cases.x : cases.y )
			: cases.z;
		return clamp( threshold , 1.0e-6, 1.0 );
	}
#endif`,PE=`#ifdef USE_ALPHAMAP
	diffuseColor.a *= texture2D( alphaMap, vAlphaMapUv ).g;
#endif`,zE=`#ifdef USE_ALPHAMAP
	uniform sampler2D alphaMap;
#endif`,FE=`#ifdef USE_ALPHATEST
	#ifdef ALPHA_TO_COVERAGE
	diffuseColor.a = smoothstep( alphaTest, alphaTest + fwidth( diffuseColor.a ), diffuseColor.a );
	if ( diffuseColor.a == 0.0 ) discard;
	#else
	if ( diffuseColor.a < alphaTest ) discard;
	#endif
#endif`,BE=`#ifdef USE_ALPHATEST
	uniform float alphaTest;
#endif`,IE=`#ifdef USE_AOMAP
	float ambientOcclusion = ( texture2D( aoMap, vAoMapUv ).r - 1.0 ) * aoMapIntensity + 1.0;
	reflectedLight.indirectDiffuse *= ambientOcclusion;
	#if defined( USE_CLEARCOAT ) 
		clearcoatSpecularIndirect *= ambientOcclusion;
	#endif
	#if defined( USE_SHEEN ) 
		sheenSpecularIndirect *= ambientOcclusion;
	#endif
	#if defined( USE_ENVMAP ) && defined( STANDARD )
		float dotNV = saturate( dot( geometryNormal, geometryViewDir ) );
		reflectedLight.indirectSpecular *= computeSpecularOcclusion( dotNV, ambientOcclusion, material.roughness );
	#endif
#endif`,HE=`#ifdef USE_AOMAP
	uniform sampler2D aoMap;
	uniform float aoMapIntensity;
#endif`,GE=`#ifdef USE_BATCHING
	#if ! defined( GL_ANGLE_multi_draw )
	#define gl_DrawID _gl_DrawID
	uniform int _gl_DrawID;
	#endif
	uniform highp sampler2D batchingTexture;
	uniform highp usampler2D batchingIdTexture;
	mat4 getBatchingMatrix( const in float i ) {
		int size = textureSize( batchingTexture, 0 ).x;
		int j = int( i ) * 4;
		int x = j % size;
		int y = j / size;
		vec4 v1 = texelFetch( batchingTexture, ivec2( x, y ), 0 );
		vec4 v2 = texelFetch( batchingTexture, ivec2( x + 1, y ), 0 );
		vec4 v3 = texelFetch( batchingTexture, ivec2( x + 2, y ), 0 );
		vec4 v4 = texelFetch( batchingTexture, ivec2( x + 3, y ), 0 );
		return mat4( v1, v2, v3, v4 );
	}
	float getIndirectIndex( const in int i ) {
		int size = textureSize( batchingIdTexture, 0 ).x;
		int x = i % size;
		int y = i / size;
		return float( texelFetch( batchingIdTexture, ivec2( x, y ), 0 ).r );
	}
#endif
#ifdef USE_BATCHING_COLOR
	uniform sampler2D batchingColorTexture;
	vec4 getBatchingColor( const in float i ) {
		int size = textureSize( batchingColorTexture, 0 ).x;
		int j = int( i );
		int x = j % size;
		int y = j / size;
		return texelFetch( batchingColorTexture, ivec2( x, y ), 0 );
	}
#endif`,VE=`#ifdef USE_BATCHING
	mat4 batchingMatrix = getBatchingMatrix( getIndirectIndex( gl_DrawID ) );
#endif`,kE=`vec3 transformed = vec3( position );
#ifdef USE_ALPHAHASH
	vPosition = vec3( position );
#endif`,XE=`vec3 objectNormal = vec3( normal );
#ifdef USE_TANGENT
	vec3 objectTangent = vec3( tangent.xyz );
#endif`,WE=`float G_BlinnPhong_Implicit( ) {
	return 0.25;
}
float D_BlinnPhong( const in float shininess, const in float dotNH ) {
	return RECIPROCAL_PI * ( shininess * 0.5 + 1.0 ) * pow( dotNH, shininess );
}
vec3 BRDF_BlinnPhong( const in vec3 lightDir, const in vec3 viewDir, const in vec3 normal, const in vec3 specularColor, const in float shininess ) {
	vec3 halfDir = normalize( lightDir + viewDir );
	float dotNH = saturate( dot( normal, halfDir ) );
	float dotVH = saturate( dot( viewDir, halfDir ) );
	vec3 F = F_Schlick( specularColor, 1.0, dotVH );
	float G = G_BlinnPhong_Implicit( );
	float D = D_BlinnPhong( shininess, dotNH );
	return F * ( G * D );
} // validated`,qE=`#ifdef USE_IRIDESCENCE
	const mat3 XYZ_TO_REC709 = mat3(
		 3.2404542, -0.9692660,  0.0556434,
		-1.5371385,  1.8760108, -0.2040259,
		-0.4985314,  0.0415560,  1.0572252
	);
	vec3 Fresnel0ToIor( vec3 fresnel0 ) {
		vec3 sqrtF0 = sqrt( fresnel0 );
		return ( vec3( 1.0 ) + sqrtF0 ) / ( vec3( 1.0 ) - sqrtF0 );
	}
	vec3 IorToFresnel0( vec3 transmittedIor, float incidentIor ) {
		return pow2( ( transmittedIor - vec3( incidentIor ) ) / ( transmittedIor + vec3( incidentIor ) ) );
	}
	float IorToFresnel0( float transmittedIor, float incidentIor ) {
		return pow2( ( transmittedIor - incidentIor ) / ( transmittedIor + incidentIor ));
	}
	vec3 evalSensitivity( float OPD, vec3 shift ) {
		float phase = 2.0 * PI * OPD * 1.0e-9;
		vec3 val = vec3( 5.4856e-13, 4.4201e-13, 5.2481e-13 );
		vec3 pos = vec3( 1.6810e+06, 1.7953e+06, 2.2084e+06 );
		vec3 var = vec3( 4.3278e+09, 9.3046e+09, 6.6121e+09 );
		vec3 xyz = val * sqrt( 2.0 * PI * var ) * cos( pos * phase + shift ) * exp( - pow2( phase ) * var );
		xyz.x += 9.7470e-14 * sqrt( 2.0 * PI * 4.5282e+09 ) * cos( 2.2399e+06 * phase + shift[ 0 ] ) * exp( - 4.5282e+09 * pow2( phase ) );
		xyz /= 1.0685e-7;
		vec3 rgb = XYZ_TO_REC709 * xyz;
		return rgb;
	}
	vec3 evalIridescence( float outsideIOR, float eta2, float cosTheta1, float thinFilmThickness, vec3 baseF0 ) {
		vec3 I;
		float iridescenceIOR = mix( outsideIOR, eta2, smoothstep( 0.0, 0.03, thinFilmThickness ) );
		float sinTheta2Sq = pow2( outsideIOR / iridescenceIOR ) * ( 1.0 - pow2( cosTheta1 ) );
		float cosTheta2Sq = 1.0 - sinTheta2Sq;
		if ( cosTheta2Sq < 0.0 ) {
			return vec3( 1.0 );
		}
		float cosTheta2 = sqrt( cosTheta2Sq );
		float R0 = IorToFresnel0( iridescenceIOR, outsideIOR );
		float R12 = F_Schlick( R0, 1.0, cosTheta1 );
		float T121 = 1.0 - R12;
		float phi12 = 0.0;
		if ( iridescenceIOR < outsideIOR ) phi12 = PI;
		float phi21 = PI - phi12;
		vec3 baseIOR = Fresnel0ToIor( clamp( baseF0, 0.0, 0.9999 ) );		vec3 R1 = IorToFresnel0( baseIOR, iridescenceIOR );
		vec3 R23 = F_Schlick( R1, 1.0, cosTheta2 );
		vec3 phi23 = vec3( 0.0 );
		if ( baseIOR[ 0 ] < iridescenceIOR ) phi23[ 0 ] = PI;
		if ( baseIOR[ 1 ] < iridescenceIOR ) phi23[ 1 ] = PI;
		if ( baseIOR[ 2 ] < iridescenceIOR ) phi23[ 2 ] = PI;
		float OPD = 2.0 * iridescenceIOR * thinFilmThickness * cosTheta2;
		vec3 phi = vec3( phi21 ) + phi23;
		vec3 R123 = clamp( R12 * R23, 1e-5, 0.9999 );
		vec3 r123 = sqrt( R123 );
		vec3 Rs = pow2( T121 ) * R23 / ( vec3( 1.0 ) - R123 );
		vec3 C0 = R12 + Rs;
		I = C0;
		vec3 Cm = Rs - T121;
		for ( int m = 1; m <= 2; ++ m ) {
			Cm *= r123;
			vec3 Sm = 2.0 * evalSensitivity( float( m ) * OPD, float( m ) * phi );
			I += Cm * Sm;
		}
		return max( I, vec3( 0.0 ) );
	}
#endif`,YE=`#ifdef USE_BUMPMAP
	uniform sampler2D bumpMap;
	uniform float bumpScale;
	vec2 dHdxy_fwd() {
		vec2 dSTdx = dFdx( vBumpMapUv );
		vec2 dSTdy = dFdy( vBumpMapUv );
		float Hll = bumpScale * texture2D( bumpMap, vBumpMapUv ).x;
		float dBx = bumpScale * texture2D( bumpMap, vBumpMapUv + dSTdx ).x - Hll;
		float dBy = bumpScale * texture2D( bumpMap, vBumpMapUv + dSTdy ).x - Hll;
		return vec2( dBx, dBy );
	}
	vec3 perturbNormalArb( vec3 surf_pos, vec3 surf_norm, vec2 dHdxy, float faceDirection ) {
		vec3 vSigmaX = normalize( dFdx( surf_pos.xyz ) );
		vec3 vSigmaY = normalize( dFdy( surf_pos.xyz ) );
		vec3 vN = surf_norm;
		vec3 R1 = cross( vSigmaY, vN );
		vec3 R2 = cross( vN, vSigmaX );
		float fDet = dot( vSigmaX, R1 ) * faceDirection;
		vec3 vGrad = sign( fDet ) * ( dHdxy.x * R1 + dHdxy.y * R2 );
		return normalize( abs( fDet ) * surf_norm - vGrad );
	}
#endif`,ZE=`#if NUM_CLIPPING_PLANES > 0
	vec4 plane;
	#ifdef ALPHA_TO_COVERAGE
		float distanceToPlane, distanceGradient;
		float clipOpacity = 1.0;
		#pragma unroll_loop_start
		for ( int i = 0; i < UNION_CLIPPING_PLANES; i ++ ) {
			plane = clippingPlanes[ i ];
			distanceToPlane = - dot( vClipPosition, plane.xyz ) + plane.w;
			distanceGradient = fwidth( distanceToPlane ) / 2.0;
			clipOpacity *= smoothstep( - distanceGradient, distanceGradient, distanceToPlane );
			if ( clipOpacity == 0.0 ) discard;
		}
		#pragma unroll_loop_end
		#if UNION_CLIPPING_PLANES < NUM_CLIPPING_PLANES
			float unionClipOpacity = 1.0;
			#pragma unroll_loop_start
			for ( int i = UNION_CLIPPING_PLANES; i < NUM_CLIPPING_PLANES; i ++ ) {
				plane = clippingPlanes[ i ];
				distanceToPlane = - dot( vClipPosition, plane.xyz ) + plane.w;
				distanceGradient = fwidth( distanceToPlane ) / 2.0;
				unionClipOpacity *= 1.0 - smoothstep( - distanceGradient, distanceGradient, distanceToPlane );
			}
			#pragma unroll_loop_end
			clipOpacity *= 1.0 - unionClipOpacity;
		#endif
		diffuseColor.a *= clipOpacity;
		if ( diffuseColor.a == 0.0 ) discard;
	#else
		#pragma unroll_loop_start
		for ( int i = 0; i < UNION_CLIPPING_PLANES; i ++ ) {
			plane = clippingPlanes[ i ];
			if ( dot( vClipPosition, plane.xyz ) > plane.w ) discard;
		}
		#pragma unroll_loop_end
		#if UNION_CLIPPING_PLANES < NUM_CLIPPING_PLANES
			bool clipped = true;
			#pragma unroll_loop_start
			for ( int i = UNION_CLIPPING_PLANES; i < NUM_CLIPPING_PLANES; i ++ ) {
				plane = clippingPlanes[ i ];
				clipped = ( dot( vClipPosition, plane.xyz ) > plane.w ) && clipped;
			}
			#pragma unroll_loop_end
			if ( clipped ) discard;
		#endif
	#endif
#endif`,KE=`#if NUM_CLIPPING_PLANES > 0
	varying vec3 vClipPosition;
	uniform vec4 clippingPlanes[ NUM_CLIPPING_PLANES ];
#endif`,QE=`#if NUM_CLIPPING_PLANES > 0
	varying vec3 vClipPosition;
#endif`,JE=`#if NUM_CLIPPING_PLANES > 0
	vClipPosition = - mvPosition.xyz;
#endif`,$E=`#if defined( USE_COLOR ) || defined( USE_COLOR_ALPHA )
	diffuseColor *= vColor;
#endif`,jE=`#if defined( USE_COLOR ) || defined( USE_COLOR_ALPHA )
	varying vec4 vColor;
#endif`,eb=`#if defined( USE_COLOR ) || defined( USE_COLOR_ALPHA ) || defined( USE_INSTANCING_COLOR ) || defined( USE_BATCHING_COLOR )
	varying vec4 vColor;
#endif`,tb=`#if defined( USE_COLOR ) || defined( USE_COLOR_ALPHA ) || defined( USE_INSTANCING_COLOR ) || defined( USE_BATCHING_COLOR )
	vColor = vec4( 1.0 );
#endif
#ifdef USE_COLOR_ALPHA
	vColor *= color;
#elif defined( USE_COLOR )
	vColor.rgb *= color;
#endif
#ifdef USE_INSTANCING_COLOR
	vColor.rgb *= instanceColor.rgb;
#endif
#ifdef USE_BATCHING_COLOR
	vColor *= getBatchingColor( getIndirectIndex( gl_DrawID ) );
#endif`,nb=`#define PI 3.141592653589793
#define PI2 6.283185307179586
#define PI_HALF 1.5707963267948966
#define RECIPROCAL_PI 0.3183098861837907
#define RECIPROCAL_PI2 0.15915494309189535
#define EPSILON 1e-6
#ifndef saturate
#define saturate( a ) clamp( a, 0.0, 1.0 )
#endif
#define whiteComplement( a ) ( 1.0 - saturate( a ) )
float pow2( const in float x ) { return x*x; }
vec3 pow2( const in vec3 x ) { return x*x; }
float pow3( const in float x ) { return x*x*x; }
float pow4( const in float x ) { float x2 = x*x; return x2*x2; }
float max3( const in vec3 v ) { return max( max( v.x, v.y ), v.z ); }
float average( const in vec3 v ) { return dot( v, vec3( 0.3333333 ) ); }
highp float rand( const in vec2 uv ) {
	const highp float a = 12.9898, b = 78.233, c = 43758.5453;
	highp float dt = dot( uv.xy, vec2( a,b ) ), sn = mod( dt, PI );
	return fract( sin( sn ) * c );
}
#ifdef HIGH_PRECISION
	float precisionSafeLength( vec3 v ) { return length( v ); }
#else
	float precisionSafeLength( vec3 v ) {
		float maxComponent = max3( abs( v ) );
		return length( v / maxComponent ) * maxComponent;
	}
#endif
struct IncidentLight {
	vec3 color;
	vec3 direction;
	bool visible;
};
struct ReflectedLight {
	vec3 directDiffuse;
	vec3 directSpecular;
	vec3 indirectDiffuse;
	vec3 indirectSpecular;
};
#ifdef USE_ALPHAHASH
	varying vec3 vPosition;
#endif
vec3 transformDirection( in vec3 dir, in mat4 matrix ) {
	return normalize( ( matrix * vec4( dir, 0.0 ) ).xyz );
}
#define inverseTransformDirection transformDirectionByInverseViewMatrix
vec3 transformNormalByInverseViewMatrix( in vec3 normal, in mat4 viewMatrix ) {
	return normalize( ( vec4( normal, 0.0 ) * viewMatrix ).xyz );
}
vec3 transformDirectionByInverseViewMatrix( in vec3 dir, in mat4 viewMatrix ) {
	return normalize( ( vec4( dir, 0.0 ) * viewMatrix ).xyz );
}
bool isPerspectiveMatrix( mat4 m ) {
	return m[ 2 ][ 3 ] == - 1.0;
}
vec2 equirectUv( in vec3 dir ) {
	float u = atan( dir.z, dir.x ) * RECIPROCAL_PI2 + 0.5;
	float v = asin( clamp( dir.y, - 1.0, 1.0 ) ) * RECIPROCAL_PI + 0.5;
	return vec2( u, v );
}
vec3 BRDF_Lambert( const in vec3 diffuseColor ) {
	return RECIPROCAL_PI * diffuseColor;
}
vec3 F_Schlick( const in vec3 f0, const in float f90, const in float dotVH ) {
	float fresnel = exp2( ( - 5.55473 * dotVH - 6.98316 ) * dotVH );
	return f0 * ( 1.0 - fresnel ) + ( f90 * fresnel );
}
float F_Schlick( const in float f0, const in float f90, const in float dotVH ) {
	float fresnel = exp2( ( - 5.55473 * dotVH - 6.98316 ) * dotVH );
	return f0 * ( 1.0 - fresnel ) + ( f90 * fresnel );
} // validated`,ib=`#ifdef ENVMAP_TYPE_CUBE_UV
	#define cubeUV_minMipLevel 4.0
	#define cubeUV_minTileSize 16.0
	float getFace( vec3 direction ) {
		vec3 absDirection = abs( direction );
		float face = - 1.0;
		if ( absDirection.x > absDirection.z ) {
			if ( absDirection.x > absDirection.y )
				face = direction.x > 0.0 ? 0.0 : 3.0;
			else
				face = direction.y > 0.0 ? 1.0 : 4.0;
		} else {
			if ( absDirection.z > absDirection.y )
				face = direction.z > 0.0 ? 2.0 : 5.0;
			else
				face = direction.y > 0.0 ? 1.0 : 4.0;
		}
		return face;
	}
	vec2 getUV( vec3 direction, float face ) {
		vec2 uv;
		if ( face == 0.0 ) {
			uv = vec2( direction.z, direction.y ) / abs( direction.x );
		} else if ( face == 1.0 ) {
			uv = vec2( - direction.x, - direction.z ) / abs( direction.y );
		} else if ( face == 2.0 ) {
			uv = vec2( - direction.x, direction.y ) / abs( direction.z );
		} else if ( face == 3.0 ) {
			uv = vec2( - direction.z, direction.y ) / abs( direction.x );
		} else if ( face == 4.0 ) {
			uv = vec2( - direction.x, direction.z ) / abs( direction.y );
		} else {
			uv = vec2( direction.x, direction.y ) / abs( direction.z );
		}
		return 0.5 * ( uv + 1.0 );
	}
	vec3 bilinearCubeUV( sampler2D envMap, vec3 direction, float mipInt ) {
		float face = getFace( direction );
		float filterInt = max( cubeUV_minMipLevel - mipInt, 0.0 );
		mipInt = max( mipInt, cubeUV_minMipLevel );
		float faceSize = exp2( mipInt );
		highp vec2 uv = getUV( direction, face ) * ( faceSize - 2.0 ) + 1.0;
		if ( face > 2.0 ) {
			uv.y += faceSize;
			face -= 3.0;
		}
		uv.x += face * faceSize;
		uv.x += filterInt * 3.0 * cubeUV_minTileSize;
		uv.y += 4.0 * ( exp2( CUBEUV_MAX_MIP ) - faceSize );
		uv.x *= CUBEUV_TEXEL_WIDTH;
		uv.y *= CUBEUV_TEXEL_HEIGHT;
		#ifdef texture2DGradEXT
			return texture2DGradEXT( envMap, uv, vec2( 0.0 ), vec2( 0.0 ) ).rgb;
		#else
			return texture2D( envMap, uv ).rgb;
		#endif
	}
	#define cubeUV_r0 1.0
	#define cubeUV_m0 - 2.0
	#define cubeUV_r1 0.8
	#define cubeUV_m1 - 1.0
	#define cubeUV_r4 0.4
	#define cubeUV_m4 2.0
	#define cubeUV_r5 0.305
	#define cubeUV_m5 3.0
	#define cubeUV_r6 0.21
	#define cubeUV_m6 4.0
	float roughnessToMip( float roughness ) {
		float mip = 0.0;
		if ( roughness >= cubeUV_r1 ) {
			mip = ( cubeUV_r0 - roughness ) * ( cubeUV_m1 - cubeUV_m0 ) / ( cubeUV_r0 - cubeUV_r1 ) + cubeUV_m0;
		} else if ( roughness >= cubeUV_r4 ) {
			mip = ( cubeUV_r1 - roughness ) * ( cubeUV_m4 - cubeUV_m1 ) / ( cubeUV_r1 - cubeUV_r4 ) + cubeUV_m1;
		} else if ( roughness >= cubeUV_r5 ) {
			mip = ( cubeUV_r4 - roughness ) * ( cubeUV_m5 - cubeUV_m4 ) / ( cubeUV_r4 - cubeUV_r5 ) + cubeUV_m4;
		} else if ( roughness >= cubeUV_r6 ) {
			mip = ( cubeUV_r5 - roughness ) * ( cubeUV_m6 - cubeUV_m5 ) / ( cubeUV_r5 - cubeUV_r6 ) + cubeUV_m5;
		} else {
			mip = - 2.0 * log2( 1.16 * roughness );		}
		return mip;
	}
	vec4 textureCubeUV( sampler2D envMap, vec3 sampleDir, float roughness ) {
		float mip = clamp( roughnessToMip( roughness ), cubeUV_m0, CUBEUV_MAX_MIP );
		float mipF = fract( mip );
		float mipInt = floor( mip );
		vec3 color0 = bilinearCubeUV( envMap, sampleDir, mipInt );
		if ( mipF == 0.0 ) {
			return vec4( color0, 1.0 );
		} else {
			vec3 color1 = bilinearCubeUV( envMap, sampleDir, mipInt + 1.0 );
			return vec4( mix( color0, color1, mipF ), 1.0 );
		}
	}
#endif`,ab=`vec3 transformedNormal = objectNormal;
#ifdef USE_TANGENT
	vec3 transformedTangent = objectTangent;
#endif
#ifdef USE_BATCHING
	mat3 bm = mat3( batchingMatrix );
	transformedNormal /= vec3( dot( bm[ 0 ], bm[ 0 ] ), dot( bm[ 1 ], bm[ 1 ] ), dot( bm[ 2 ], bm[ 2 ] ) );
	transformedNormal = bm * transformedNormal;
	#ifdef USE_TANGENT
		transformedTangent = bm * transformedTangent;
	#endif
#endif
#ifdef USE_INSTANCING
	mat3 im = mat3( instanceMatrix );
	transformedNormal /= vec3( dot( im[ 0 ], im[ 0 ] ), dot( im[ 1 ], im[ 1 ] ), dot( im[ 2 ], im[ 2 ] ) );
	transformedNormal = im * transformedNormal;
	#ifdef USE_TANGENT
		transformedTangent = im * transformedTangent;
	#endif
#endif
transformedNormal = normalMatrix * transformedNormal;
#ifdef FLIP_SIDED
	transformedNormal = - transformedNormal;
#endif
#ifdef USE_TANGENT
	transformedTangent = ( modelViewMatrix * vec4( transformedTangent, 0.0 ) ).xyz;
#endif`,sb=`#ifdef USE_DISPLACEMENTMAP
	uniform sampler2D displacementMap;
	uniform float displacementScale;
	uniform float displacementBias;
#endif`,rb=`#ifdef USE_DISPLACEMENTMAP
	transformed += normalize( objectNormal ) * ( texture2D( displacementMap, vDisplacementMapUv ).x * displacementScale + displacementBias );
#endif`,ob=`#ifdef USE_EMISSIVEMAP
	vec4 emissiveColor = texture2D( emissiveMap, vEmissiveMapUv );
	#ifdef DECODE_VIDEO_TEXTURE_EMISSIVE
		emissiveColor = sRGBTransferEOTF( emissiveColor );
	#endif
	totalEmissiveRadiance *= emissiveColor.rgb;
#endif`,lb=`#ifdef USE_EMISSIVEMAP
	uniform sampler2D emissiveMap;
#endif`,cb="gl_FragColor = linearToOutputTexel( gl_FragColor );",ub=`vec4 LinearTransferOETF( in vec4 value ) {
	return value;
}
vec4 sRGBTransferEOTF( in vec4 value ) {
	return vec4( mix( pow( value.rgb * 0.9478672986 + vec3( 0.0521327014 ), vec3( 2.4 ) ), value.rgb * 0.0773993808, vec3( lessThanEqual( value.rgb, vec3( 0.04045 ) ) ) ), value.a );
}
vec4 sRGBTransferOETF( in vec4 value ) {
	return vec4( mix( pow( value.rgb, vec3( 0.41666 ) ) * 1.055 - vec3( 0.055 ), value.rgb * 12.92, vec3( lessThanEqual( value.rgb, vec3( 0.0031308 ) ) ) ), value.a );
}`,fb=`#ifdef USE_ENVMAP
	#ifdef ENV_WORLDPOS
		vec3 cameraToFrag;
		if ( isOrthographic ) {
			cameraToFrag = normalize( vec3( - viewMatrix[ 0 ][ 2 ], - viewMatrix[ 1 ][ 2 ], - viewMatrix[ 2 ][ 2 ] ) );
		} else {
			cameraToFrag = normalize( vWorldPosition - cameraPosition );
		}
		vec3 worldNormal = transformNormalByInverseViewMatrix( normal, viewMatrix );
		#ifdef ENVMAP_MODE_REFLECTION
			vec3 reflectVec = reflect( cameraToFrag, worldNormal );
		#else
			vec3 reflectVec = refract( cameraToFrag, worldNormal, refractionRatio );
		#endif
	#else
		vec3 reflectVec = vReflect;
	#endif
	#ifdef ENVMAP_TYPE_CUBE
		vec4 envColor = textureCube( envMap, envMapRotation * reflectVec );
		#ifdef ENVMAP_BLENDING_MULTIPLY
			outgoingLight = mix( outgoingLight, outgoingLight * envColor.xyz, specularStrength * reflectivity );
		#elif defined( ENVMAP_BLENDING_MIX )
			outgoingLight = mix( outgoingLight, envColor.xyz, specularStrength * reflectivity );
		#elif defined( ENVMAP_BLENDING_ADD )
			outgoingLight += envColor.xyz * specularStrength * reflectivity;
		#endif
	#endif
#endif`,db=`#ifdef USE_ENVMAP
	uniform float envMapIntensity;
	uniform mat3 envMapRotation;
	#ifdef ENVMAP_TYPE_CUBE
		uniform samplerCube envMap;
	#else
		uniform sampler2D envMap;
	#endif
#endif`,hb=`#ifdef USE_ENVMAP
	uniform float reflectivity;
	#if defined( USE_BUMPMAP ) || defined( USE_NORMALMAP ) || defined( PHONG ) || defined( LAMBERT )
		#define ENV_WORLDPOS
	#endif
	#ifdef ENV_WORLDPOS
		varying vec3 vWorldPosition;
		uniform float refractionRatio;
	#else
		varying vec3 vReflect;
	#endif
#endif`,pb=`#ifdef USE_ENVMAP
	#if defined( USE_BUMPMAP ) || defined( USE_NORMALMAP ) || defined( PHONG ) || defined( LAMBERT )
		#define ENV_WORLDPOS
	#endif
	#ifdef ENV_WORLDPOS
		
		varying vec3 vWorldPosition;
	#else
		varying vec3 vReflect;
		uniform float refractionRatio;
	#endif
#endif`,mb=`#ifdef USE_ENVMAP
	#ifdef ENV_WORLDPOS
		vWorldPosition = worldPosition.xyz;
	#else
		vec3 cameraToVertex;
		if ( isOrthographic ) {
			cameraToVertex = normalize( vec3( - viewMatrix[ 0 ][ 2 ], - viewMatrix[ 1 ][ 2 ], - viewMatrix[ 2 ][ 2 ] ) );
		} else {
			cameraToVertex = normalize( worldPosition.xyz - cameraPosition );
		}
		vec3 worldNormal = transformNormalByInverseViewMatrix( transformedNormal, viewMatrix );
		#ifdef ENVMAP_MODE_REFLECTION
			vReflect = reflect( cameraToVertex, worldNormal );
		#else
			vReflect = refract( cameraToVertex, worldNormal, refractionRatio );
		#endif
	#endif
#endif`,gb=`#ifdef USE_FOG
	vFogDepth = - mvPosition.z;
#endif`,_b=`#ifdef USE_FOG
	varying float vFogDepth;
#endif`,vb=`#ifdef USE_FOG
	#ifdef FOG_EXP2
		float fogFactor = 1.0 - exp( - fogDensity * fogDensity * vFogDepth * vFogDepth );
	#else
		float fogFactor = smoothstep( fogNear, fogFar, vFogDepth );
	#endif
	gl_FragColor.rgb = mix( gl_FragColor.rgb, fogColor, fogFactor );
#endif`,xb=`#ifdef USE_FOG
	uniform vec3 fogColor;
	varying float vFogDepth;
	#ifdef FOG_EXP2
		uniform float fogDensity;
	#else
		uniform float fogNear;
		uniform float fogFar;
	#endif
#endif`,Sb=`#ifdef USE_GRADIENTMAP
	uniform sampler2D gradientMap;
#endif
vec3 getGradientIrradiance( vec3 normal, vec3 lightDirection ) {
	float dotNL = dot( normal, lightDirection );
	vec2 coord = vec2( dotNL * 0.5 + 0.5, 0.0 );
	#ifdef USE_GRADIENTMAP
		return vec3( texture2D( gradientMap, coord ).r );
	#else
		vec2 fw = fwidth( coord ) * 0.5;
		return mix( vec3( 0.7 ), vec3( 1.0 ), smoothstep( 0.7 - fw.x, 0.7 + fw.x, coord.x ) );
	#endif
}`,Mb=`#ifdef USE_LIGHTMAP
	uniform sampler2D lightMap;
	uniform float lightMapIntensity;
#endif`,yb=`LambertMaterial material;
material.diffuseColor = diffuseColor.rgb;
material.specularStrength = specularStrength;`,Eb=`varying vec3 vViewPosition;
struct LambertMaterial {
	vec3 diffuseColor;
	float specularStrength;
};
void RE_Direct_Lambert( const in IncidentLight directLight, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in LambertMaterial material, inout ReflectedLight reflectedLight ) {
	float dotNL = saturate( dot( geometryNormal, directLight.direction ) );
	vec3 irradiance = dotNL * directLight.color;
	reflectedLight.directDiffuse += irradiance * BRDF_Lambert( material.diffuseColor );
}
void RE_IndirectDiffuse_Lambert( const in vec3 irradiance, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in LambertMaterial material, inout ReflectedLight reflectedLight ) {
	reflectedLight.indirectDiffuse += irradiance * BRDF_Lambert( material.diffuseColor );
}
#define RE_Direct				RE_Direct_Lambert
#define RE_IndirectDiffuse		RE_IndirectDiffuse_Lambert`,bb=`uniform bool receiveShadow;
uniform vec3 ambientLightColor;
#if defined( USE_LIGHT_PROBES )
	uniform vec3 lightProbe[ 9 ];
#endif
vec3 shGetIrradianceAt( in vec3 normal, in vec3 shCoefficients[ 9 ] ) {
	float x = normal.x, y = normal.y, z = normal.z;
	vec3 result = shCoefficients[ 0 ] * 0.886227;
	result += shCoefficients[ 1 ] * 2.0 * 0.511664 * y;
	result += shCoefficients[ 2 ] * 2.0 * 0.511664 * z;
	result += shCoefficients[ 3 ] * 2.0 * 0.511664 * x;
	result += shCoefficients[ 4 ] * 2.0 * 0.429043 * x * y;
	result += shCoefficients[ 5 ] * 2.0 * 0.429043 * y * z;
	result += shCoefficients[ 6 ] * ( 0.743125 * z * z - 0.247708 );
	result += shCoefficients[ 7 ] * 2.0 * 0.429043 * x * z;
	result += shCoefficients[ 8 ] * 0.429043 * ( x * x - y * y );
	return result;
}
vec3 getLightProbeIrradiance( const in vec3 lightProbe[ 9 ], const in vec3 normal ) {
	vec3 worldNormal = transformNormalByInverseViewMatrix( normal, viewMatrix );
	vec3 irradiance = shGetIrradianceAt( worldNormal, lightProbe );
	return irradiance;
}
vec3 getAmbientLightIrradiance( const in vec3 ambientLightColor ) {
	vec3 irradiance = ambientLightColor;
	return irradiance;
}
float getDistanceAttenuation( const in float lightDistance, const in float cutoffDistance, const in float decayExponent ) {
	float distanceFalloff = 1.0 / max( pow( lightDistance, decayExponent ), 0.01 );
	if ( cutoffDistance > 0.0 ) {
		distanceFalloff *= pow2( saturate( 1.0 - pow4( lightDistance / cutoffDistance ) ) );
	}
	return distanceFalloff;
}
float getSpotAttenuation( const in float coneCosine, const in float penumbraCosine, const in float angleCosine ) {
	return smoothstep( coneCosine, penumbraCosine, angleCosine );
}
#if NUM_DIR_LIGHTS > 0
	struct DirectionalLight {
		vec3 direction;
		vec3 color;
	};
	uniform DirectionalLight directionalLights[ NUM_DIR_LIGHTS ];
	void getDirectionalLightInfo( const in DirectionalLight directionalLight, out IncidentLight light ) {
		light.color = directionalLight.color;
		light.direction = directionalLight.direction;
		light.visible = true;
	}
#endif
#if NUM_POINT_LIGHTS > 0
	struct PointLight {
		vec3 position;
		vec3 color;
		float distance;
		float decay;
	};
	uniform PointLight pointLights[ NUM_POINT_LIGHTS ];
	void getPointLightInfo( const in PointLight pointLight, const in vec3 geometryPosition, out IncidentLight light ) {
		vec3 lVector = pointLight.position - geometryPosition;
		light.direction = normalize( lVector );
		float lightDistance = length( lVector );
		light.color = pointLight.color;
		light.color *= getDistanceAttenuation( lightDistance, pointLight.distance, pointLight.decay );
		light.visible = ( light.color != vec3( 0.0 ) );
	}
#endif
#if NUM_SPOT_LIGHTS > 0
	struct SpotLight {
		vec3 position;
		vec3 direction;
		vec3 color;
		float distance;
		float decay;
		float coneCos;
		float penumbraCos;
	};
	uniform SpotLight spotLights[ NUM_SPOT_LIGHTS ];
	void getSpotLightInfo( const in SpotLight spotLight, const in vec3 geometryPosition, out IncidentLight light ) {
		vec3 lVector = spotLight.position - geometryPosition;
		light.direction = normalize( lVector );
		float angleCos = dot( light.direction, spotLight.direction );
		float spotAttenuation = getSpotAttenuation( spotLight.coneCos, spotLight.penumbraCos, angleCos );
		if ( spotAttenuation > 0.0 ) {
			float lightDistance = length( lVector );
			light.color = spotLight.color * spotAttenuation;
			light.color *= getDistanceAttenuation( lightDistance, spotLight.distance, spotLight.decay );
			light.visible = ( light.color != vec3( 0.0 ) );
		} else {
			light.color = vec3( 0.0 );
			light.visible = false;
		}
	}
#endif
#if NUM_RECT_AREA_LIGHTS > 0
	struct RectAreaLight {
		vec3 color;
		vec3 position;
		vec3 halfWidth;
		vec3 halfHeight;
	};
	uniform sampler2D ltc_1;	uniform sampler2D ltc_2;
	uniform RectAreaLight rectAreaLights[ NUM_RECT_AREA_LIGHTS ];
#endif
#if NUM_HEMI_LIGHTS > 0
	struct HemisphereLight {
		vec3 direction;
		vec3 skyColor;
		vec3 groundColor;
	};
	uniform HemisphereLight hemisphereLights[ NUM_HEMI_LIGHTS ];
	vec3 getHemisphereLightIrradiance( const in HemisphereLight hemiLight, const in vec3 normal ) {
		float dotNL = dot( normal, hemiLight.direction );
		float hemiDiffuseWeight = 0.5 * dotNL + 0.5;
		vec3 irradiance = mix( hemiLight.groundColor, hemiLight.skyColor, hemiDiffuseWeight );
		return irradiance;
	}
#endif
#include <lightprobes_pars_fragment>`,Tb=`#ifdef USE_ENVMAP
	vec3 getIBLIrradiance( const in vec3 normal ) {
		#ifdef ENVMAP_TYPE_CUBE_UV
			vec3 worldNormal = transformNormalByInverseViewMatrix( normal, viewMatrix );
			vec4 envMapColor = textureCubeUV( envMap, envMapRotation * worldNormal, 1.0 );
			return PI * envMapColor.rgb * envMapIntensity;
		#else
			return vec3( 0.0 );
		#endif
	}
	vec3 getIBLRadiance( const in vec3 viewDir, const in vec3 normal, const in float roughness ) {
		#ifdef ENVMAP_TYPE_CUBE_UV
			vec3 reflectVec = reflect( - viewDir, normal );
			reflectVec = normalize( mix( reflectVec, normal, pow4( roughness ) ) );
			reflectVec = transformDirectionByInverseViewMatrix( reflectVec, viewMatrix );
			vec4 envMapColor = textureCubeUV( envMap, envMapRotation * reflectVec, roughness );
			return envMapColor.rgb * envMapIntensity;
		#else
			return vec3( 0.0 );
		#endif
	}
	#ifdef USE_ANISOTROPY
		vec3 getIBLAnisotropyRadiance( const in vec3 viewDir, const in vec3 normal, const in float roughness, const in vec3 bitangent, const in float anisotropy ) {
			#ifdef ENVMAP_TYPE_CUBE_UV
				vec3 bentNormal = cross( bitangent, viewDir );
				bentNormal = normalize( cross( bentNormal, bitangent ) );
				bentNormal = normalize( mix( bentNormal, normal, pow2( pow2( 1.0 - anisotropy * ( 1.0 - roughness ) ) ) ) );
				return getIBLRadiance( viewDir, bentNormal, roughness );
			#else
				return vec3( 0.0 );
			#endif
		}
	#endif
#endif`,Ab=`ToonMaterial material;
material.diffuseColor = diffuseColor.rgb;`,Rb=`varying vec3 vViewPosition;
struct ToonMaterial {
	vec3 diffuseColor;
};
void RE_Direct_Toon( const in IncidentLight directLight, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in ToonMaterial material, inout ReflectedLight reflectedLight ) {
	vec3 irradiance = getGradientIrradiance( geometryNormal, directLight.direction ) * directLight.color;
	reflectedLight.directDiffuse += irradiance * BRDF_Lambert( material.diffuseColor );
}
void RE_IndirectDiffuse_Toon( const in vec3 irradiance, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in ToonMaterial material, inout ReflectedLight reflectedLight ) {
	reflectedLight.indirectDiffuse += irradiance * BRDF_Lambert( material.diffuseColor );
}
#define RE_Direct				RE_Direct_Toon
#define RE_IndirectDiffuse		RE_IndirectDiffuse_Toon`,Cb=`BlinnPhongMaterial material;
material.diffuseColor = diffuseColor.rgb;
material.specularColor = specular;
material.specularShininess = shininess;
material.specularStrength = specularStrength;`,wb=`varying vec3 vViewPosition;
struct BlinnPhongMaterial {
	vec3 diffuseColor;
	vec3 specularColor;
	float specularShininess;
	float specularStrength;
};
void RE_Direct_BlinnPhong( const in IncidentLight directLight, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in BlinnPhongMaterial material, inout ReflectedLight reflectedLight ) {
	float dotNL = saturate( dot( geometryNormal, directLight.direction ) );
	vec3 irradiance = dotNL * directLight.color;
	reflectedLight.directDiffuse += irradiance * BRDF_Lambert( material.diffuseColor );
	reflectedLight.directSpecular += irradiance * BRDF_BlinnPhong( directLight.direction, geometryViewDir, geometryNormal, material.specularColor, material.specularShininess ) * material.specularStrength;
}
void RE_IndirectDiffuse_BlinnPhong( const in vec3 irradiance, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in BlinnPhongMaterial material, inout ReflectedLight reflectedLight ) {
	reflectedLight.indirectDiffuse += irradiance * BRDF_Lambert( material.diffuseColor );
}
#define RE_Direct				RE_Direct_BlinnPhong
#define RE_IndirectDiffuse		RE_IndirectDiffuse_BlinnPhong`,Db=`PhysicalMaterial material;
material.diffuseColor = diffuseColor.rgb;
material.diffuseContribution = diffuseColor.rgb * ( 1.0 - metalnessFactor );
material.metalness = metalnessFactor;
vec3 dxy = max( abs( dFdx( nonPerturbedNormal ) ), abs( dFdy( nonPerturbedNormal ) ) );
float geometryRoughness = max( max( dxy.x, dxy.y ), dxy.z );
material.roughness = max( roughnessFactor, 0.0525 );material.roughness += geometryRoughness;
material.roughness = min( material.roughness, 1.0 );
#ifdef IOR
	material.ior = ior;
	#ifdef USE_SPECULAR
		float specularIntensityFactor = specularIntensity;
		vec3 specularColorFactor = specularColor;
		#ifdef USE_SPECULAR_COLORMAP
			specularColorFactor *= texture2D( specularColorMap, vSpecularColorMapUv ).rgb;
		#endif
		#ifdef USE_SPECULAR_INTENSITYMAP
			specularIntensityFactor *= texture2D( specularIntensityMap, vSpecularIntensityMapUv ).a;
		#endif
		material.specularF90 = mix( specularIntensityFactor, 1.0, metalnessFactor );
	#else
		float specularIntensityFactor = 1.0;
		vec3 specularColorFactor = vec3( 1.0 );
		material.specularF90 = 1.0;
	#endif
	material.specularColor = min( pow2( ( material.ior - 1.0 ) / ( material.ior + 1.0 ) ) * specularColorFactor, vec3( 1.0 ) ) * specularIntensityFactor;
	material.specularColorBlended = mix( material.specularColor, diffuseColor.rgb, metalnessFactor );
#else
	material.specularColor = vec3( 0.04 );
	material.specularColorBlended = mix( material.specularColor, diffuseColor.rgb, metalnessFactor );
	material.specularF90 = 1.0;
#endif
#ifdef USE_CLEARCOAT
	material.clearcoat = clearcoat;
	material.clearcoatRoughness = clearcoatRoughness;
	material.clearcoatF0 = vec3( 0.04 );
	material.clearcoatF90 = 1.0;
	#ifdef USE_CLEARCOATMAP
		material.clearcoat *= texture2D( clearcoatMap, vClearcoatMapUv ).x;
	#endif
	#ifdef USE_CLEARCOAT_ROUGHNESSMAP
		material.clearcoatRoughness *= texture2D( clearcoatRoughnessMap, vClearcoatRoughnessMapUv ).y;
	#endif
	material.clearcoat = saturate( material.clearcoat );	material.clearcoatRoughness = max( material.clearcoatRoughness, 0.0525 );
	material.clearcoatRoughness += geometryRoughness;
	material.clearcoatRoughness = min( material.clearcoatRoughness, 1.0 );
#endif
#ifdef USE_DISPERSION
	material.dispersion = dispersion;
#endif
#ifdef USE_IRIDESCENCE
	material.iridescence = iridescence;
	material.iridescenceIOR = iridescenceIOR;
	#ifdef USE_IRIDESCENCEMAP
		material.iridescence *= texture2D( iridescenceMap, vIridescenceMapUv ).r;
	#endif
	#ifdef USE_IRIDESCENCE_THICKNESSMAP
		material.iridescenceThickness = (iridescenceThicknessMaximum - iridescenceThicknessMinimum) * texture2D( iridescenceThicknessMap, vIridescenceThicknessMapUv ).g + iridescenceThicknessMinimum;
	#else
		material.iridescenceThickness = iridescenceThicknessMaximum;
	#endif
#endif
#ifdef USE_SHEEN
	material.sheenColor = sheenColor;
	#ifdef USE_SHEEN_COLORMAP
		material.sheenColor *= texture2D( sheenColorMap, vSheenColorMapUv ).rgb;
	#endif
	material.sheenRoughness = clamp( sheenRoughness, 0.0001, 1.0 );
	#ifdef USE_SHEEN_ROUGHNESSMAP
		material.sheenRoughness *= texture2D( sheenRoughnessMap, vSheenRoughnessMapUv ).a;
	#endif
#endif
#ifdef USE_ANISOTROPY
	#ifdef USE_ANISOTROPYMAP
		mat2 anisotropyMat = mat2( anisotropyVector.x, anisotropyVector.y, - anisotropyVector.y, anisotropyVector.x );
		vec3 anisotropyPolar = texture2D( anisotropyMap, vAnisotropyMapUv ).rgb;
		vec2 anisotropyV = anisotropyMat * normalize( 2.0 * anisotropyPolar.rg - vec2( 1.0 ) ) * anisotropyPolar.b;
	#else
		vec2 anisotropyV = anisotropyVector;
	#endif
	material.anisotropy = length( anisotropyV );
	if( material.anisotropy == 0.0 ) {
		anisotropyV = vec2( 1.0, 0.0 );
	} else {
		anisotropyV /= material.anisotropy;
		material.anisotropy = saturate( material.anisotropy );
	}
	material.alphaT = mix( pow2( material.roughness ), 1.0, pow2( material.anisotropy ) );
	material.anisotropyT = tbn[ 0 ] * anisotropyV.x + tbn[ 1 ] * anisotropyV.y;
	material.anisotropyB = tbn[ 1 ] * anisotropyV.x - tbn[ 0 ] * anisotropyV.y;
#endif`,Ub=`uniform sampler2D dfgLUT;
struct PhysicalMaterial {
	vec3 diffuseColor;
	vec3 diffuseContribution;
	vec3 specularColor;
	vec3 specularColorBlended;
	float roughness;
	float metalness;
	float specularF90;
	float dispersion;
	#ifdef USE_CLEARCOAT
		float clearcoat;
		float clearcoatRoughness;
		vec3 clearcoatF0;
		float clearcoatF90;
	#endif
	#ifdef USE_IRIDESCENCE
		float iridescence;
		float iridescenceIOR;
		float iridescenceThickness;
		vec3 iridescenceFresnel;
		vec3 iridescenceF0;
		vec3 iridescenceFresnelDielectric;
		vec3 iridescenceFresnelMetallic;
	#endif
	#ifdef USE_SHEEN
		vec3 sheenColor;
		float sheenRoughness;
	#endif
	#ifdef IOR
		float ior;
	#endif
	#ifdef USE_TRANSMISSION
		float transmission;
		float transmissionAlpha;
		float thickness;
		float attenuationDistance;
		vec3 attenuationColor;
	#endif
	#ifdef USE_ANISOTROPY
		float anisotropy;
		float alphaT;
		vec3 anisotropyT;
		vec3 anisotropyB;
	#endif
};
vec3 clearcoatSpecularDirect = vec3( 0.0 );
vec3 clearcoatSpecularIndirect = vec3( 0.0 );
vec3 sheenSpecularDirect = vec3( 0.0 );
vec3 sheenSpecularIndirect = vec3(0.0 );
vec3 Schlick_to_F0( const in vec3 f, const in float f90, const in float dotVH ) {
    float x = clamp( 1.0 - dotVH, 0.0, 1.0 );
    float x2 = x * x;
    float x5 = clamp( x * x2 * x2, 0.0, 0.9999 );
    return ( f - vec3( f90 ) * x5 ) / ( 1.0 - x5 );
}
float V_GGX_SmithCorrelated( const in float alpha, const in float dotNL, const in float dotNV ) {
	float a2 = pow2( alpha );
	float gv = dotNL * sqrt( a2 + ( 1.0 - a2 ) * pow2( dotNV ) );
	float gl = dotNV * sqrt( a2 + ( 1.0 - a2 ) * pow2( dotNL ) );
	return 0.5 / max( gv + gl, EPSILON );
}
float D_GGX( const in float alpha, const in float dotNH ) {
	float a2 = pow2( alpha );
	float denom = pow2( dotNH ) * ( a2 - 1.0 ) + 1.0;
	return RECIPROCAL_PI * a2 / pow2( denom );
}
#ifdef USE_ANISOTROPY
	float V_GGX_SmithCorrelated_Anisotropic( const in float alphaT, const in float alphaB, const in float dotTV, const in float dotBV, const in float dotTL, const in float dotBL, const in float dotNV, const in float dotNL ) {
		float gv = dotNL * length( vec3( alphaT * dotTV, alphaB * dotBV, dotNV ) );
		float gl = dotNV * length( vec3( alphaT * dotTL, alphaB * dotBL, dotNL ) );
		return 0.5 / max( gv + gl, EPSILON );
	}
	float D_GGX_Anisotropic( const in float alphaT, const in float alphaB, const in float dotNH, const in float dotTH, const in float dotBH ) {
		float a2 = alphaT * alphaB;
		highp vec3 v = vec3( alphaB * dotTH, alphaT * dotBH, a2 * dotNH );
		highp float v2 = dot( v, v );
		float w2 = a2 / v2;
		return RECIPROCAL_PI * a2 * pow2 ( w2 );
	}
#endif
#ifdef USE_CLEARCOAT
	vec3 BRDF_GGX_Clearcoat( const in vec3 lightDir, const in vec3 viewDir, const in vec3 normal, const in PhysicalMaterial material) {
		vec3 f0 = material.clearcoatF0;
		float f90 = material.clearcoatF90;
		float roughness = material.clearcoatRoughness;
		float alpha = pow2( roughness );
		vec3 halfDir = normalize( lightDir + viewDir );
		float dotNL = saturate( dot( normal, lightDir ) );
		float dotNV = saturate( dot( normal, viewDir ) );
		float dotNH = saturate( dot( normal, halfDir ) );
		float dotVH = saturate( dot( viewDir, halfDir ) );
		vec3 F = F_Schlick( f0, f90, dotVH );
		float V = V_GGX_SmithCorrelated( alpha, dotNL, dotNV );
		float D = D_GGX( alpha, dotNH );
		return F * ( V * D );
	}
#endif
vec3 BRDF_GGX( const in vec3 lightDir, const in vec3 viewDir, const in vec3 normal, const in PhysicalMaterial material ) {
	vec3 f0 = material.specularColorBlended;
	float f90 = material.specularF90;
	float roughness = material.roughness;
	float alpha = pow2( roughness );
	vec3 halfDir = normalize( lightDir + viewDir );
	float dotNL = saturate( dot( normal, lightDir ) );
	float dotNV = saturate( dot( normal, viewDir ) );
	float dotNH = saturate( dot( normal, halfDir ) );
	float dotVH = saturate( dot( viewDir, halfDir ) );
	vec3 F = F_Schlick( f0, f90, dotVH );
	#ifdef USE_IRIDESCENCE
		F = mix( F, material.iridescenceFresnel, material.iridescence );
	#endif
	#ifdef USE_ANISOTROPY
		float dotTL = dot( material.anisotropyT, lightDir );
		float dotTV = dot( material.anisotropyT, viewDir );
		float dotTH = dot( material.anisotropyT, halfDir );
		float dotBL = dot( material.anisotropyB, lightDir );
		float dotBV = dot( material.anisotropyB, viewDir );
		float dotBH = dot( material.anisotropyB, halfDir );
		float V = V_GGX_SmithCorrelated_Anisotropic( material.alphaT, alpha, dotTV, dotBV, dotTL, dotBL, dotNV, dotNL );
		float D = D_GGX_Anisotropic( material.alphaT, alpha, dotNH, dotTH, dotBH );
	#else
		float V = V_GGX_SmithCorrelated( alpha, dotNL, dotNV );
		float D = D_GGX( alpha, dotNH );
	#endif
	return F * ( V * D );
}
vec2 LTC_Uv( const in vec3 N, const in vec3 V, const in float roughness ) {
	const float LUT_SIZE = 64.0;
	const float LUT_SCALE = ( LUT_SIZE - 1.0 ) / LUT_SIZE;
	const float LUT_BIAS = 0.5 / LUT_SIZE;
	float dotNV = saturate( dot( N, V ) );
	vec2 uv = vec2( roughness, sqrt( 1.0 - dotNV ) );
	uv = uv * LUT_SCALE + LUT_BIAS;
	return uv;
}
float LTC_ClippedSphereFormFactor( const in vec3 f ) {
	float l = length( f );
	return max( ( l * l + f.z ) / ( l + 1.0 ), 0.0 );
}
vec3 LTC_EdgeVectorFormFactor( const in vec3 v1, const in vec3 v2 ) {
	float x = dot( v1, v2 );
	float y = abs( x );
	float a = 0.8543985 + ( 0.4965155 + 0.0145206 * y ) * y;
	float b = 3.4175940 + ( 4.1616724 + y ) * y;
	float v = a / b;
	float theta_sintheta = ( x > 0.0 ) ? v : 0.5 * inversesqrt( max( 1.0 - x * x, 1e-7 ) ) - v;
	return cross( v1, v2 ) * theta_sintheta;
}
vec3 LTC_Evaluate( const in vec3 N, const in vec3 V, const in vec3 P, const in mat3 mInv, const in vec3 rectCoords[ 4 ] ) {
	vec3 v1 = rectCoords[ 1 ] - rectCoords[ 0 ];
	vec3 v2 = rectCoords[ 3 ] - rectCoords[ 0 ];
	vec3 lightNormal = cross( v1, v2 );
	if( dot( lightNormal, P - rectCoords[ 0 ] ) < 0.0 ) return vec3( 0.0 );
	vec3 T1, T2;
	T1 = normalize( V - N * dot( V, N ) );
	T2 = - cross( N, T1 );
	mat3 mat = mInv * transpose( mat3( T1, T2, N ) );
	vec3 coords[ 4 ];
	coords[ 0 ] = mat * ( rectCoords[ 0 ] - P );
	coords[ 1 ] = mat * ( rectCoords[ 1 ] - P );
	coords[ 2 ] = mat * ( rectCoords[ 2 ] - P );
	coords[ 3 ] = mat * ( rectCoords[ 3 ] - P );
	coords[ 0 ] = normalize( coords[ 0 ] );
	coords[ 1 ] = normalize( coords[ 1 ] );
	coords[ 2 ] = normalize( coords[ 2 ] );
	coords[ 3 ] = normalize( coords[ 3 ] );
	vec3 vectorFormFactor = vec3( 0.0 );
	vectorFormFactor += LTC_EdgeVectorFormFactor( coords[ 0 ], coords[ 1 ] );
	vectorFormFactor += LTC_EdgeVectorFormFactor( coords[ 1 ], coords[ 2 ] );
	vectorFormFactor += LTC_EdgeVectorFormFactor( coords[ 2 ], coords[ 3 ] );
	vectorFormFactor += LTC_EdgeVectorFormFactor( coords[ 3 ], coords[ 0 ] );
	float result = LTC_ClippedSphereFormFactor( vectorFormFactor );
	return vec3( result );
}
#if defined( USE_SHEEN )
float D_Charlie( float roughness, float dotNH ) {
	float alpha = pow2( roughness );
	float invAlpha = 1.0 / alpha;
	float cos2h = dotNH * dotNH;
	float sin2h = max( 1.0 - cos2h, 0.0078125 );
	return ( 2.0 + invAlpha ) * pow( sin2h, invAlpha * 0.5 ) / ( 2.0 * PI );
}
float V_Neubelt( float dotNV, float dotNL ) {
	return saturate( 1.0 / ( 4.0 * ( dotNL + dotNV - dotNL * dotNV ) ) );
}
vec3 BRDF_Sheen( const in vec3 lightDir, const in vec3 viewDir, const in vec3 normal, vec3 sheenColor, const in float sheenRoughness ) {
	vec3 halfDir = normalize( lightDir + viewDir );
	float dotNL = saturate( dot( normal, lightDir ) );
	float dotNV = saturate( dot( normal, viewDir ) );
	float dotNH = saturate( dot( normal, halfDir ) );
	float D = D_Charlie( sheenRoughness, dotNH );
	float V = V_Neubelt( dotNV, dotNL );
	return sheenColor * ( D * V );
}
#endif
float IBLSheenBRDF( const in vec3 normal, const in vec3 viewDir, const in float roughness ) {
	float dotNV = saturate( dot( normal, viewDir ) );
	float r2 = roughness * roughness;
	float rInv = 1.0 / ( roughness + 0.1 );
	float a = -1.9362 + 1.0678 * roughness + 0.4573 * r2 - 0.8469 * rInv;
	float b = -0.6014 + 0.5538 * roughness - 0.4670 * r2 - 0.1255 * rInv;
	float DG = exp( a * dotNV + b );
	return saturate( DG );
}
vec3 EnvironmentBRDF( const in vec3 normal, const in vec3 viewDir, const in vec3 specularColor, const in float specularF90, const in float roughness ) {
	float dotNV = saturate( dot( normal, viewDir ) );
	vec2 fab = texture2D( dfgLUT, vec2( roughness, dotNV ) ).rg;
	return specularColor * fab.x + specularF90 * fab.y;
}
#ifdef USE_IRIDESCENCE
void computeMultiscatteringIridescence( const in vec3 normal, const in vec3 viewDir, const in vec3 specularColor, const in float specularF90, const in float iridescence, const in vec3 iridescenceF0, const in float roughness, inout vec3 singleScatter, inout vec3 multiScatter ) {
#else
void computeMultiscattering( const in vec3 normal, const in vec3 viewDir, const in vec3 specularColor, const in float specularF90, const in float roughness, inout vec3 singleScatter, inout vec3 multiScatter ) {
#endif
	float dotNV = saturate( dot( normal, viewDir ) );
	vec2 fab = texture2D( dfgLUT, vec2( roughness, dotNV ) ).rg;
	#ifdef USE_IRIDESCENCE
		vec3 Fr = mix( specularColor, iridescenceF0, iridescence );
	#else
		vec3 Fr = specularColor;
	#endif
	vec3 FssEss = Fr * fab.x + specularF90 * fab.y;
	float Ess = fab.x + fab.y;
	float Ems = 1.0 - Ess;
	vec3 Favg = Fr + ( 1.0 - Fr ) * 0.047619;	vec3 Fms = FssEss * Favg / ( 1.0 - Ems * Favg );
	singleScatter += FssEss;
	multiScatter += Fms * Ems;
}
vec3 BRDF_GGX_Multiscatter( const in vec3 lightDir, const in vec3 viewDir, const in vec3 normal, const in PhysicalMaterial material ) {
	vec3 singleScatter = BRDF_GGX( lightDir, viewDir, normal, material );
	float dotNL = saturate( dot( normal, lightDir ) );
	float dotNV = saturate( dot( normal, viewDir ) );
	vec2 dfgV = texture2D( dfgLUT, vec2( material.roughness, dotNV ) ).rg;
	vec2 dfgL = texture2D( dfgLUT, vec2( material.roughness, dotNL ) ).rg;
	vec3 FssEss_V = material.specularColorBlended * dfgV.x + material.specularF90 * dfgV.y;
	vec3 FssEss_L = material.specularColorBlended * dfgL.x + material.specularF90 * dfgL.y;
	float Ess_V = dfgV.x + dfgV.y;
	float Ess_L = dfgL.x + dfgL.y;
	float Ems_V = 1.0 - Ess_V;
	float Ems_L = 1.0 - Ess_L;
	vec3 Favg = material.specularColorBlended + ( 1.0 - material.specularColorBlended ) * 0.047619;
	vec3 Fms = FssEss_V * FssEss_L * Favg / ( 1.0 - Ems_V * Ems_L * Favg + EPSILON );
	float compensationFactor = Ems_V * Ems_L;
	vec3 multiScatter = Fms * compensationFactor;
	return singleScatter + multiScatter;
}
#if NUM_RECT_AREA_LIGHTS > 0
	void RE_Direct_RectArea_Physical( const in RectAreaLight rectAreaLight, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in PhysicalMaterial material, inout ReflectedLight reflectedLight ) {
		vec3 normal = geometryNormal;
		vec3 viewDir = geometryViewDir;
		vec3 position = geometryPosition;
		vec3 lightPos = rectAreaLight.position;
		vec3 halfWidth = rectAreaLight.halfWidth;
		vec3 halfHeight = rectAreaLight.halfHeight;
		vec3 lightColor = rectAreaLight.color;
		float roughness = material.roughness;
		vec3 rectCoords[ 4 ];
		rectCoords[ 0 ] = lightPos + halfWidth - halfHeight;		rectCoords[ 1 ] = lightPos - halfWidth - halfHeight;
		rectCoords[ 2 ] = lightPos - halfWidth + halfHeight;
		rectCoords[ 3 ] = lightPos + halfWidth + halfHeight;
		vec2 uv = LTC_Uv( normal, viewDir, roughness );
		vec4 t1 = texture2D( ltc_1, uv );
		vec4 t2 = texture2D( ltc_2, uv );
		mat3 mInv = mat3(
			vec3( t1.x, 0, t1.y ),
			vec3(    0, 1,    0 ),
			vec3( t1.z, 0, t1.w )
		);
		vec3 fresnel = ( material.specularColorBlended * t2.x + ( material.specularF90 - material.specularColorBlended ) * t2.y );
		reflectedLight.directSpecular += lightColor * fresnel * LTC_Evaluate( normal, viewDir, position, mInv, rectCoords );
		reflectedLight.directDiffuse += lightColor * material.diffuseContribution * LTC_Evaluate( normal, viewDir, position, mat3( 1.0 ), rectCoords );
		#ifdef USE_CLEARCOAT
			vec3 Ncc = geometryClearcoatNormal;
			vec2 uvClearcoat = LTC_Uv( Ncc, viewDir, material.clearcoatRoughness );
			vec4 t1Clearcoat = texture2D( ltc_1, uvClearcoat );
			vec4 t2Clearcoat = texture2D( ltc_2, uvClearcoat );
			mat3 mInvClearcoat = mat3(
				vec3( t1Clearcoat.x, 0, t1Clearcoat.y ),
				vec3(             0, 1,             0 ),
				vec3( t1Clearcoat.z, 0, t1Clearcoat.w )
			);
			vec3 fresnelClearcoat = material.clearcoatF0 * t2Clearcoat.x + ( material.clearcoatF90 - material.clearcoatF0 ) * t2Clearcoat.y;
			clearcoatSpecularDirect += lightColor * fresnelClearcoat * LTC_Evaluate( Ncc, viewDir, position, mInvClearcoat, rectCoords );
		#endif
	}
#endif
void RE_Direct_Physical( const in IncidentLight directLight, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in PhysicalMaterial material, inout ReflectedLight reflectedLight ) {
	float dotNL = saturate( dot( geometryNormal, directLight.direction ) );
	vec3 irradiance = dotNL * directLight.color;
	#ifdef USE_CLEARCOAT
		float dotNLcc = saturate( dot( geometryClearcoatNormal, directLight.direction ) );
		vec3 ccIrradiance = dotNLcc * directLight.color;
		clearcoatSpecularDirect += ccIrradiance * BRDF_GGX_Clearcoat( directLight.direction, geometryViewDir, geometryClearcoatNormal, material );
	#endif
	#ifdef USE_SHEEN
 
 		sheenSpecularDirect += irradiance * BRDF_Sheen( directLight.direction, geometryViewDir, geometryNormal, material.sheenColor, material.sheenRoughness );
 
 		float sheenAlbedoV = IBLSheenBRDF( geometryNormal, geometryViewDir, material.sheenRoughness );
 		float sheenAlbedoL = IBLSheenBRDF( geometryNormal, directLight.direction, material.sheenRoughness );
 
 		float sheenEnergyComp = 1.0 - max3( material.sheenColor ) * max( sheenAlbedoV, sheenAlbedoL );
 
 		irradiance *= sheenEnergyComp;
 
 	#endif
	reflectedLight.directSpecular += irradiance * BRDF_GGX_Multiscatter( directLight.direction, geometryViewDir, geometryNormal, material );
	reflectedLight.directDiffuse += irradiance * BRDF_Lambert( material.diffuseContribution );
}
void RE_IndirectDiffuse_Physical( const in vec3 irradiance, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in PhysicalMaterial material, inout ReflectedLight reflectedLight ) {
	vec3 diffuse = irradiance * BRDF_Lambert( material.diffuseContribution );
	#ifdef USE_SHEEN
		float sheenAlbedo = IBLSheenBRDF( geometryNormal, geometryViewDir, material.sheenRoughness );
		float sheenEnergyComp = 1.0 - max3( material.sheenColor ) * sheenAlbedo;
		diffuse *= sheenEnergyComp;
	#endif
	reflectedLight.indirectDiffuse += diffuse;
}
void RE_IndirectSpecular_Physical( const in vec3 radiance, const in vec3 irradiance, const in vec3 clearcoatRadiance, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in PhysicalMaterial material, inout ReflectedLight reflectedLight) {
	#ifdef USE_CLEARCOAT
		clearcoatSpecularIndirect += clearcoatRadiance * EnvironmentBRDF( geometryClearcoatNormal, geometryViewDir, material.clearcoatF0, material.clearcoatF90, material.clearcoatRoughness );
	#endif
	#ifdef USE_SHEEN
		sheenSpecularIndirect += irradiance * material.sheenColor * IBLSheenBRDF( geometryNormal, geometryViewDir, material.sheenRoughness ) * RECIPROCAL_PI;
 	#endif
	vec3 singleScatteringDielectric = vec3( 0.0 );
	vec3 multiScatteringDielectric = vec3( 0.0 );
	vec3 singleScatteringMetallic = vec3( 0.0 );
	vec3 multiScatteringMetallic = vec3( 0.0 );
	#ifdef USE_IRIDESCENCE
		computeMultiscatteringIridescence( geometryNormal, geometryViewDir, material.specularColor, material.specularF90, material.iridescence, material.iridescenceFresnelDielectric, material.roughness, singleScatteringDielectric, multiScatteringDielectric );
		computeMultiscatteringIridescence( geometryNormal, geometryViewDir, material.diffuseColor, material.specularF90, material.iridescence, material.iridescenceFresnelMetallic, material.roughness, singleScatteringMetallic, multiScatteringMetallic );
	#else
		computeMultiscattering( geometryNormal, geometryViewDir, material.specularColor, material.specularF90, material.roughness, singleScatteringDielectric, multiScatteringDielectric );
		computeMultiscattering( geometryNormal, geometryViewDir, material.diffuseColor, material.specularF90, material.roughness, singleScatteringMetallic, multiScatteringMetallic );
	#endif
	vec3 singleScattering = mix( singleScatteringDielectric, singleScatteringMetallic, material.metalness );
	vec3 multiScattering = mix( multiScatteringDielectric, multiScatteringMetallic, material.metalness );
	vec3 totalScatteringDielectric = singleScatteringDielectric + multiScatteringDielectric;
	vec3 diffuse = material.diffuseContribution * ( 1.0 - totalScatteringDielectric );
	vec3 cosineWeightedIrradiance = irradiance * RECIPROCAL_PI;
	vec3 indirectSpecular = radiance * singleScattering;
	indirectSpecular += multiScattering * cosineWeightedIrradiance;
	vec3 indirectDiffuse = diffuse * cosineWeightedIrradiance;
	#ifdef USE_SHEEN
		float sheenAlbedo = IBLSheenBRDF( geometryNormal, geometryViewDir, material.sheenRoughness );
		float sheenEnergyComp = 1.0 - max3( material.sheenColor ) * sheenAlbedo;
		indirectSpecular *= sheenEnergyComp;
		indirectDiffuse *= sheenEnergyComp;
	#endif
	reflectedLight.indirectSpecular += indirectSpecular;
	reflectedLight.indirectDiffuse += indirectDiffuse;
}
#define RE_Direct				RE_Direct_Physical
#define RE_Direct_RectArea		RE_Direct_RectArea_Physical
#define RE_IndirectDiffuse		RE_IndirectDiffuse_Physical
#define RE_IndirectSpecular		RE_IndirectSpecular_Physical
float computeSpecularOcclusion( const in float dotNV, const in float ambientOcclusion, const in float roughness ) {
	return saturate( pow( dotNV + ambientOcclusion, exp2( - 16.0 * roughness - 1.0 ) ) - 1.0 + ambientOcclusion );
}`,Lb=`
vec3 geometryPosition = - vViewPosition;
vec3 geometryNormal = normal;
vec3 geometryViewDir = ( isOrthographic ) ? vec3( 0, 0, 1 ) : normalize( vViewPosition );
vec3 geometryClearcoatNormal = vec3( 0.0 );
#ifdef USE_CLEARCOAT
	geometryClearcoatNormal = clearcoatNormal;
#endif
#ifdef USE_IRIDESCENCE
	float dotNVi = saturate( dot( normal, geometryViewDir ) );
	if ( material.iridescenceThickness == 0.0 ) {
		material.iridescence = 0.0;
	} else {
		material.iridescence = saturate( material.iridescence );
	}
	if ( material.iridescence > 0.0 ) {
		material.iridescenceFresnelDielectric = evalIridescence( 1.0, material.iridescenceIOR, dotNVi, material.iridescenceThickness, material.specularColor );
		material.iridescenceFresnelMetallic = evalIridescence( 1.0, material.iridescenceIOR, dotNVi, material.iridescenceThickness, material.diffuseColor );
		material.iridescenceFresnel = mix( material.iridescenceFresnelDielectric, material.iridescenceFresnelMetallic, material.metalness );
		material.iridescenceF0 = Schlick_to_F0( material.iridescenceFresnel, 1.0, dotNVi );
	}
#endif
IncidentLight directLight;
#if ( NUM_POINT_LIGHTS > 0 ) && defined( RE_Direct )
	PointLight pointLight;
	#if defined( USE_SHADOWMAP ) && NUM_POINT_LIGHT_SHADOWS > 0
	PointLightShadow pointLightShadow;
	#endif
	#pragma unroll_loop_start
	for ( int i = 0; i < NUM_POINT_LIGHTS; i ++ ) {
		pointLight = pointLights[ i ];
		getPointLightInfo( pointLight, geometryPosition, directLight );
		#if defined( USE_SHADOWMAP ) && ( UNROLLED_LOOP_INDEX < NUM_POINT_LIGHT_SHADOWS ) && ( defined( SHADOWMAP_TYPE_PCF ) || defined( SHADOWMAP_TYPE_BASIC ) )
		pointLightShadow = pointLightShadows[ i ];
		directLight.color *= ( directLight.visible && receiveShadow ) ? getPointShadow( pointShadowMap[ i ], pointLightShadow.shadowMapSize, pointLightShadow.shadowIntensity, pointLightShadow.shadowBias, pointLightShadow.shadowRadius, vPointShadowCoord[ i ], pointLightShadow.shadowCameraNear, pointLightShadow.shadowCameraFar ) : 1.0;
		#endif
		RE_Direct( directLight, geometryPosition, geometryNormal, geometryViewDir, geometryClearcoatNormal, material, reflectedLight );
	}
	#pragma unroll_loop_end
#endif
#if ( NUM_SPOT_LIGHTS > 0 ) && defined( RE_Direct )
	SpotLight spotLight;
	vec4 spotColor;
	vec3 spotLightCoord;
	bool inSpotLightMap;
	#if defined( USE_SHADOWMAP ) && NUM_SPOT_LIGHT_SHADOWS > 0
	SpotLightShadow spotLightShadow;
	#endif
	#pragma unroll_loop_start
	for ( int i = 0; i < NUM_SPOT_LIGHTS; i ++ ) {
		spotLight = spotLights[ i ];
		getSpotLightInfo( spotLight, geometryPosition, directLight );
		#if ( UNROLLED_LOOP_INDEX < NUM_SPOT_LIGHT_SHADOWS_WITH_MAPS )
		#define SPOT_LIGHT_MAP_INDEX UNROLLED_LOOP_INDEX
		#elif ( UNROLLED_LOOP_INDEX < NUM_SPOT_LIGHT_SHADOWS )
		#define SPOT_LIGHT_MAP_INDEX NUM_SPOT_LIGHT_MAPS
		#else
		#define SPOT_LIGHT_MAP_INDEX ( UNROLLED_LOOP_INDEX - NUM_SPOT_LIGHT_SHADOWS + NUM_SPOT_LIGHT_SHADOWS_WITH_MAPS )
		#endif
		#if ( SPOT_LIGHT_MAP_INDEX < NUM_SPOT_LIGHT_MAPS )
			spotLightCoord = vSpotLightCoord[ i ].xyz / vSpotLightCoord[ i ].w;
			inSpotLightMap = all( lessThan( abs( spotLightCoord * 2. - 1. ), vec3( 1.0 ) ) );
			spotColor = texture2D( spotLightMap[ SPOT_LIGHT_MAP_INDEX ], spotLightCoord.xy );
			directLight.color = inSpotLightMap ? directLight.color * spotColor.rgb : directLight.color;
		#endif
		#undef SPOT_LIGHT_MAP_INDEX
		#if defined( USE_SHADOWMAP ) && ( UNROLLED_LOOP_INDEX < NUM_SPOT_LIGHT_SHADOWS )
		spotLightShadow = spotLightShadows[ i ];
		directLight.color *= ( directLight.visible && receiveShadow ) ? getShadow( spotShadowMap[ i ], spotLightShadow.shadowMapSize, spotLightShadow.shadowIntensity, spotLightShadow.shadowBias, spotLightShadow.shadowRadius, vSpotLightCoord[ i ] ) : 1.0;
		#endif
		RE_Direct( directLight, geometryPosition, geometryNormal, geometryViewDir, geometryClearcoatNormal, material, reflectedLight );
	}
	#pragma unroll_loop_end
#endif
#if ( NUM_DIR_LIGHTS > 0 ) && defined( RE_Direct )
	DirectionalLight directionalLight;
	#if defined( USE_SHADOWMAP ) && NUM_DIR_LIGHT_SHADOWS > 0
	DirectionalLightShadow directionalLightShadow;
	#endif
	#pragma unroll_loop_start
	for ( int i = 0; i < NUM_DIR_LIGHTS; i ++ ) {
		directionalLight = directionalLights[ i ];
		getDirectionalLightInfo( directionalLight, directLight );
		#if defined( USE_SHADOWMAP ) && ( UNROLLED_LOOP_INDEX < NUM_DIR_LIGHT_SHADOWS )
		directionalLightShadow = directionalLightShadows[ i ];
		directLight.color *= ( directLight.visible && receiveShadow ) ? getShadow( directionalShadowMap[ i ], directionalLightShadow.shadowMapSize, directionalLightShadow.shadowIntensity, directionalLightShadow.shadowBias, directionalLightShadow.shadowRadius, vDirectionalShadowCoord[ i ] ) : 1.0;
		#endif
		RE_Direct( directLight, geometryPosition, geometryNormal, geometryViewDir, geometryClearcoatNormal, material, reflectedLight );
	}
	#pragma unroll_loop_end
#endif
#if ( NUM_RECT_AREA_LIGHTS > 0 ) && defined( RE_Direct_RectArea )
	RectAreaLight rectAreaLight;
	#pragma unroll_loop_start
	for ( int i = 0; i < NUM_RECT_AREA_LIGHTS; i ++ ) {
		rectAreaLight = rectAreaLights[ i ];
		RE_Direct_RectArea( rectAreaLight, geometryPosition, geometryNormal, geometryViewDir, geometryClearcoatNormal, material, reflectedLight );
	}
	#pragma unroll_loop_end
#endif
#if defined( RE_IndirectDiffuse )
	vec3 iblIrradiance = vec3( 0.0 );
	vec3 irradiance = getAmbientLightIrradiance( ambientLightColor );
	#if defined( USE_LIGHT_PROBES )
		irradiance += getLightProbeIrradiance( lightProbe, geometryNormal );
	#endif
	#if ( NUM_HEMI_LIGHTS > 0 )
		#pragma unroll_loop_start
		for ( int i = 0; i < NUM_HEMI_LIGHTS; i ++ ) {
			irradiance += getHemisphereLightIrradiance( hemisphereLights[ i ], geometryNormal );
		}
		#pragma unroll_loop_end
	#endif
	#ifdef USE_LIGHT_PROBES_GRID
		vec3 probeWorldPos = ( ( vec4( geometryPosition, 1.0 ) - viewMatrix[ 3 ] ) * viewMatrix ).xyz;
		vec3 probeWorldNormal = transformNormalByInverseViewMatrix( geometryNormal, viewMatrix );
		irradiance += getLightProbeGridIrradiance( probeWorldPos, probeWorldNormal );
	#endif
#endif
#if defined( RE_IndirectSpecular )
	vec3 radiance = vec3( 0.0 );
	vec3 clearcoatRadiance = vec3( 0.0 );
#endif`,Nb=`#if defined( RE_IndirectDiffuse )
	#ifdef USE_LIGHTMAP
		vec4 lightMapTexel = texture2D( lightMap, vLightMapUv );
		vec3 lightMapIrradiance = lightMapTexel.rgb * lightMapIntensity;
		irradiance += lightMapIrradiance;
	#endif
	#if defined( USE_ENVMAP ) && defined( ENVMAP_TYPE_CUBE_UV )
		#if defined( STANDARD ) || defined( LAMBERT ) || defined( PHONG )
			iblIrradiance += getIBLIrradiance( geometryNormal );
		#endif
	#endif
#endif
#if defined( USE_ENVMAP ) && defined( RE_IndirectSpecular )
	#ifdef USE_ANISOTROPY
		radiance += getIBLAnisotropyRadiance( geometryViewDir, geometryNormal, material.roughness, material.anisotropyB, material.anisotropy );
	#else
		radiance += getIBLRadiance( geometryViewDir, geometryNormal, material.roughness );
	#endif
	#ifdef USE_CLEARCOAT
		clearcoatRadiance += getIBLRadiance( geometryViewDir, geometryClearcoatNormal, material.clearcoatRoughness );
	#endif
#endif`,Ob=`#if defined( RE_IndirectDiffuse )
	#if defined( LAMBERT ) || defined( PHONG )
		irradiance += iblIrradiance;
	#endif
	RE_IndirectDiffuse( irradiance, geometryPosition, geometryNormal, geometryViewDir, geometryClearcoatNormal, material, reflectedLight );
#endif
#if defined( RE_IndirectSpecular )
	RE_IndirectSpecular( radiance, iblIrradiance, clearcoatRadiance, geometryPosition, geometryNormal, geometryViewDir, geometryClearcoatNormal, material, reflectedLight );
#endif`,Pb=`#ifdef USE_LIGHT_PROBES_GRID
uniform highp sampler3D probesSH;
uniform vec3 probesMin;
uniform vec3 probesMax;
uniform vec3 probesResolution;
vec3 getLightProbeGridIrradiance( vec3 worldPos, vec3 worldNormal ) {
	vec3 res = probesResolution;
	vec3 gridRange = probesMax - probesMin;
	vec3 resMinusOne = res - 1.0;
	vec3 probeSpacing = gridRange / resMinusOne;
	vec3 samplePos = worldPos + worldNormal * probeSpacing * 0.5;
	vec3 uvw = clamp( ( samplePos - probesMin ) / gridRange, 0.0, 1.0 );
	uvw = uvw * resMinusOne / res + 0.5 / res;
	float nz          = res.z;
	float paddedSlices = nz + 2.0;
	float atlasDepth  = 7.0 * paddedSlices;
	float uvZBase     = uvw.z * nz + 1.0;
	vec4 s0 = texture( probesSH, vec3( uvw.xy, ( uvZBase                       ) / atlasDepth ) );
	vec4 s1 = texture( probesSH, vec3( uvw.xy, ( uvZBase +       paddedSlices   ) / atlasDepth ) );
	vec4 s2 = texture( probesSH, vec3( uvw.xy, ( uvZBase + 2.0 * paddedSlices   ) / atlasDepth ) );
	vec4 s3 = texture( probesSH, vec3( uvw.xy, ( uvZBase + 3.0 * paddedSlices   ) / atlasDepth ) );
	vec4 s4 = texture( probesSH, vec3( uvw.xy, ( uvZBase + 4.0 * paddedSlices   ) / atlasDepth ) );
	vec4 s5 = texture( probesSH, vec3( uvw.xy, ( uvZBase + 5.0 * paddedSlices   ) / atlasDepth ) );
	vec4 s6 = texture( probesSH, vec3( uvw.xy, ( uvZBase + 6.0 * paddedSlices   ) / atlasDepth ) );
	vec3 c0 = s0.xyz;
	vec3 c1 = vec3( s0.w, s1.xy );
	vec3 c2 = vec3( s1.zw, s2.x );
	vec3 c3 = s2.yzw;
	vec3 c4 = s3.xyz;
	vec3 c5 = vec3( s3.w, s4.xy );
	vec3 c6 = vec3( s4.zw, s5.x );
	vec3 c7 = s5.yzw;
	vec3 c8 = s6.xyz;
	float x = worldNormal.x, y = worldNormal.y, z = worldNormal.z;
	vec3 result = c0 * 0.886227;
	result += c1 * 2.0 * 0.511664 * y;
	result += c2 * 2.0 * 0.511664 * z;
	result += c3 * 2.0 * 0.511664 * x;
	result += c4 * 2.0 * 0.429043 * x * y;
	result += c5 * 2.0 * 0.429043 * y * z;
	result += c6 * ( 0.743125 * z * z - 0.247708 );
	result += c7 * 2.0 * 0.429043 * x * z;
	result += c8 * 0.429043 * ( x * x - y * y );
	return max( result, vec3( 0.0 ) );
}
#endif`,zb=`#if defined( USE_LOGARITHMIC_DEPTH_BUFFER )
	gl_FragDepth = vIsPerspective == 0.0 ? gl_FragCoord.z : log2( vFragDepth ) * logDepthBufFC * 0.5;
#endif`,Fb=`#if defined( USE_LOGARITHMIC_DEPTH_BUFFER )
	uniform float logDepthBufFC;
	varying float vFragDepth;
	varying float vIsPerspective;
#endif`,Bb=`#ifdef USE_LOGARITHMIC_DEPTH_BUFFER
	varying float vFragDepth;
	varying float vIsPerspective;
#endif`,Ib=`#ifdef USE_LOGARITHMIC_DEPTH_BUFFER
	vFragDepth = 1.0 + gl_Position.w;
	vIsPerspective = float( isPerspectiveMatrix( projectionMatrix ) );
#endif`,Hb=`#ifdef USE_MAP
	vec4 sampledDiffuseColor = texture2D( map, vMapUv );
	#ifdef DECODE_VIDEO_TEXTURE
		sampledDiffuseColor = sRGBTransferEOTF( sampledDiffuseColor );
	#endif
	diffuseColor *= sampledDiffuseColor;
#endif`,Gb=`#ifdef USE_MAP
	uniform sampler2D map;
#endif`,Vb=`#if defined( USE_MAP ) || defined( USE_ALPHAMAP )
	#if defined( USE_POINTS_UV )
		vec2 uv = vUv;
	#else
		vec2 uv = ( uvTransform * vec3( gl_PointCoord.x, 1.0 - gl_PointCoord.y, 1 ) ).xy;
	#endif
#endif
#ifdef USE_MAP
	diffuseColor *= texture2D( map, uv );
#endif
#ifdef USE_ALPHAMAP
	diffuseColor.a *= texture2D( alphaMap, uv ).g;
#endif`,kb=`#if defined( USE_POINTS_UV )
	varying vec2 vUv;
#else
	#if defined( USE_MAP ) || defined( USE_ALPHAMAP )
		uniform mat3 uvTransform;
	#endif
#endif
#ifdef USE_MAP
	uniform sampler2D map;
#endif
#ifdef USE_ALPHAMAP
	uniform sampler2D alphaMap;
#endif`,Xb=`float metalnessFactor = metalness;
#ifdef USE_METALNESSMAP
	vec4 texelMetalness = texture2D( metalnessMap, vMetalnessMapUv );
	metalnessFactor *= texelMetalness.b;
#endif`,Wb=`#ifdef USE_METALNESSMAP
	uniform sampler2D metalnessMap;
#endif`,qb=`#ifdef USE_INSTANCING_MORPH
	float morphTargetInfluences[ MORPHTARGETS_COUNT ];
	float morphTargetBaseInfluence = texelFetch( morphTexture, ivec2( 0, gl_InstanceID ), 0 ).r;
	for ( int i = 0; i < MORPHTARGETS_COUNT; i ++ ) {
		morphTargetInfluences[i] =  texelFetch( morphTexture, ivec2( i + 1, gl_InstanceID ), 0 ).r;
	}
#endif`,Yb=`#if defined( USE_MORPHCOLORS )
	vColor *= morphTargetBaseInfluence;
	for ( int i = 0; i < MORPHTARGETS_COUNT; i ++ ) {
		#if defined( USE_COLOR_ALPHA )
			if ( morphTargetInfluences[ i ] != 0.0 ) vColor += getMorph( gl_VertexID, i, 2 ) * morphTargetInfluences[ i ];
		#elif defined( USE_COLOR )
			if ( morphTargetInfluences[ i ] != 0.0 ) vColor += getMorph( gl_VertexID, i, 2 ).rgb * morphTargetInfluences[ i ];
		#endif
	}
#endif`,Zb=`#ifdef USE_MORPHNORMALS
	objectNormal *= morphTargetBaseInfluence;
	for ( int i = 0; i < MORPHTARGETS_COUNT; i ++ ) {
		if ( morphTargetInfluences[ i ] != 0.0 ) objectNormal += getMorph( gl_VertexID, i, 1 ).xyz * morphTargetInfluences[ i ];
	}
#endif`,Kb=`#ifdef USE_MORPHTARGETS
	#ifndef USE_INSTANCING_MORPH
		uniform float morphTargetBaseInfluence;
		uniform float morphTargetInfluences[ MORPHTARGETS_COUNT ];
	#endif
	uniform sampler2DArray morphTargetsTexture;
	uniform ivec2 morphTargetsTextureSize;
	vec4 getMorph( const in int vertexIndex, const in int morphTargetIndex, const in int offset ) {
		int texelIndex = vertexIndex * MORPHTARGETS_TEXTURE_STRIDE + offset;
		int y = texelIndex / morphTargetsTextureSize.x;
		int x = texelIndex - y * morphTargetsTextureSize.x;
		ivec3 morphUV = ivec3( x, y, morphTargetIndex );
		return texelFetch( morphTargetsTexture, morphUV, 0 );
	}
#endif`,Qb=`#ifdef USE_MORPHTARGETS
	transformed *= morphTargetBaseInfluence;
	for ( int i = 0; i < MORPHTARGETS_COUNT; i ++ ) {
		if ( morphTargetInfluences[ i ] != 0.0 ) transformed += getMorph( gl_VertexID, i, 0 ).xyz * morphTargetInfluences[ i ];
	}
#endif`,Jb=`float faceDirection = gl_FrontFacing ? 1.0 : - 1.0;
#ifdef FLAT_SHADED
	vec3 fdx = dFdx( vViewPosition );
	vec3 fdy = dFdy( vViewPosition );
	vec3 normal = normalize( cross( fdx, fdy ) );
#else
	vec3 normal = normalize( vNormal );
	#ifdef DOUBLE_SIDED
		normal *= faceDirection;
	#endif
#endif
#if defined( USE_NORMALMAP_TANGENTSPACE ) || defined( USE_CLEARCOAT_NORMALMAP ) || defined( USE_ANISOTROPY )
	#ifdef USE_TANGENT
		mat3 tbn = mat3( normalize( vTangent ), normalize( vBitangent ), normal );
	#else
		mat3 tbn = getTangentFrame( - vViewPosition, normal,
		#if defined( USE_NORMALMAP )
			vNormalMapUv
		#elif defined( USE_CLEARCOAT_NORMALMAP )
			vClearcoatNormalMapUv
		#else
			vUv
		#endif
		);
	#endif
	#ifdef DOUBLE_SIDED
		tbn[0] *= faceDirection;
		tbn[1] *= faceDirection;
	#endif
#endif
#ifdef USE_CLEARCOAT_NORMALMAP
	#ifdef USE_TANGENT
		mat3 tbn2 = mat3( normalize( vTangent ), normalize( vBitangent ), normal );
	#else
		mat3 tbn2 = getTangentFrame( - vViewPosition, normal, vClearcoatNormalMapUv );
	#endif
	#ifdef DOUBLE_SIDED
		tbn2[0] *= faceDirection;
		tbn2[1] *= faceDirection;
	#endif
#endif
vec3 nonPerturbedNormal = normal;`,$b=`#ifdef USE_NORMALMAP_OBJECTSPACE
	normal = texture2D( normalMap, vNormalMapUv ).xyz * 2.0 - 1.0;
	#ifdef FLIP_SIDED
		normal = - normal;
	#endif
	#ifdef DOUBLE_SIDED
		normal = normal * faceDirection;
	#endif
	normal = normalize( normalMatrix * normal );
#elif defined( USE_NORMALMAP_TANGENTSPACE )
	vec3 mapN = texture2D( normalMap, vNormalMapUv ).xyz * 2.0 - 1.0;
	#if defined( USE_PACKED_NORMALMAP )
		mapN = vec3( mapN.xy, sqrt( saturate( 1.0 - dot( mapN.xy, mapN.xy ) ) ) );
	#endif
	mapN.xy *= normalScale;
	normal = normalize( tbn * mapN );
#elif defined( USE_BUMPMAP )
	normal = perturbNormalArb( - vViewPosition, normal, dHdxy_fwd(), faceDirection );
#endif`,jb=`#ifndef FLAT_SHADED
	varying vec3 vNormal;
	#ifdef USE_TANGENT
		varying vec3 vTangent;
		varying vec3 vBitangent;
	#endif
#endif`,eT=`#ifndef FLAT_SHADED
	varying vec3 vNormal;
	#ifdef USE_TANGENT
		varying vec3 vTangent;
		varying vec3 vBitangent;
	#endif
#endif`,tT=`#ifndef FLAT_SHADED
	vNormal = normalize( transformedNormal );
	#ifdef USE_TANGENT
		vTangent = normalize( transformedTangent );
		vBitangent = normalize( cross( vNormal, vTangent ) * tangent.w );
		#ifdef FLIP_SIDED
			vBitangent = - vBitangent;
		#endif
	#endif
#endif`,nT=`#ifdef USE_NORMALMAP
	uniform sampler2D normalMap;
	uniform vec2 normalScale;
#endif
#ifdef USE_NORMALMAP_OBJECTSPACE
	uniform mat3 normalMatrix;
#endif
#if ! defined ( USE_TANGENT ) && ( defined ( USE_NORMALMAP_TANGENTSPACE ) || defined ( USE_CLEARCOAT_NORMALMAP ) || defined( USE_ANISOTROPY ) )
	mat3 getTangentFrame( vec3 eye_pos, vec3 surf_norm, vec2 uv ) {
		vec3 q0 = dFdx( eye_pos.xyz );
		vec3 q1 = dFdy( eye_pos.xyz );
		vec2 st0 = dFdx( uv.st );
		vec2 st1 = dFdy( uv.st );
		vec3 N = surf_norm;
		vec3 q1perp = cross( q1, N );
		vec3 q0perp = cross( N, q0 );
		vec3 T = q1perp * st0.x + q0perp * st1.x;
		vec3 B = q1perp * st0.y + q0perp * st1.y;
		float det = max( dot( T, T ), dot( B, B ) );
		float scale = ( det == 0.0 ) ? 0.0 : inversesqrt( det );
		return mat3( T * scale, B * scale, N );
	}
#endif`,iT=`#ifdef USE_CLEARCOAT
	vec3 clearcoatNormal = nonPerturbedNormal;
#endif`,aT=`#ifdef USE_CLEARCOAT_NORMALMAP
	vec3 clearcoatMapN = texture2D( clearcoatNormalMap, vClearcoatNormalMapUv ).xyz * 2.0 - 1.0;
	clearcoatMapN.xy *= clearcoatNormalScale;
	clearcoatNormal = normalize( tbn2 * clearcoatMapN );
#endif`,sT=`#ifdef USE_CLEARCOATMAP
	uniform sampler2D clearcoatMap;
#endif
#ifdef USE_CLEARCOAT_NORMALMAP
	uniform sampler2D clearcoatNormalMap;
	uniform vec2 clearcoatNormalScale;
#endif
#ifdef USE_CLEARCOAT_ROUGHNESSMAP
	uniform sampler2D clearcoatRoughnessMap;
#endif`,rT=`#ifdef USE_IRIDESCENCEMAP
	uniform sampler2D iridescenceMap;
#endif
#ifdef USE_IRIDESCENCE_THICKNESSMAP
	uniform sampler2D iridescenceThicknessMap;
#endif`,oT=`#ifdef OPAQUE
diffuseColor.a = 1.0;
#endif
#ifdef USE_TRANSMISSION
diffuseColor.a *= material.transmissionAlpha;
#endif
gl_FragColor = vec4( outgoingLight, diffuseColor.a );`,lT=`vec3 packNormalToRGB( const in vec3 normal ) {
	return normalize( normal ) * 0.5 + 0.5;
}
vec3 unpackRGBToNormal( const in vec3 rgb ) {
	return 2.0 * rgb.xyz - 1.0;
}
const float PackUpscale = 256. / 255.;const float UnpackDownscale = 255. / 256.;const float ShiftRight8 = 1. / 256.;
const float Inv255 = 1. / 255.;
const vec4 PackFactors = vec4( 1.0, 256.0, 256.0 * 256.0, 256.0 * 256.0 * 256.0 );
const vec2 UnpackFactors2 = vec2( UnpackDownscale, 1.0 / PackFactors.g );
const vec3 UnpackFactors3 = vec3( UnpackDownscale / PackFactors.rg, 1.0 / PackFactors.b );
const vec4 UnpackFactors4 = vec4( UnpackDownscale / PackFactors.rgb, 1.0 / PackFactors.a );
vec4 packDepthToRGBA( const in float v ) {
	if( v <= 0.0 )
		return vec4( 0., 0., 0., 0. );
	if( v >= 1.0 )
		return vec4( 1., 1., 1., 1. );
	float vuf;
	float af = modf( v * PackFactors.a, vuf );
	float bf = modf( vuf * ShiftRight8, vuf );
	float gf = modf( vuf * ShiftRight8, vuf );
	return vec4( vuf * Inv255, gf * PackUpscale, bf * PackUpscale, af );
}
vec3 packDepthToRGB( const in float v ) {
	if( v <= 0.0 )
		return vec3( 0., 0., 0. );
	if( v >= 1.0 )
		return vec3( 1., 1., 1. );
	float vuf;
	float bf = modf( v * PackFactors.b, vuf );
	float gf = modf( vuf * ShiftRight8, vuf );
	return vec3( vuf * Inv255, gf * PackUpscale, bf );
}
vec2 packDepthToRG( const in float v ) {
	if( v <= 0.0 )
		return vec2( 0., 0. );
	if( v >= 1.0 )
		return vec2( 1., 1. );
	float vuf;
	float gf = modf( v * 256., vuf );
	return vec2( vuf * Inv255, gf );
}
float unpackRGBAToDepth( const in vec4 v ) {
	return dot( v, UnpackFactors4 );
}
float unpackRGBToDepth( const in vec3 v ) {
	return dot( v, UnpackFactors3 );
}
float unpackRGToDepth( const in vec2 v ) {
	return v.r * UnpackFactors2.r + v.g * UnpackFactors2.g;
}
vec4 pack2HalfToRGBA( const in vec2 v ) {
	vec4 r = vec4( v.x, fract( v.x * 255.0 ), v.y, fract( v.y * 255.0 ) );
	return vec4( r.x - r.y / 255.0, r.y, r.z - r.w / 255.0, r.w );
}
vec2 unpackRGBATo2Half( const in vec4 v ) {
	return vec2( v.x + ( v.y / 255.0 ), v.z + ( v.w / 255.0 ) );
}
float viewZToOrthographicDepth( const in float viewZ, const in float near, const in float far ) {
	return ( viewZ + near ) / ( near - far );
}
float orthographicDepthToViewZ( const in float depth, const in float near, const in float far ) {
	#ifdef USE_REVERSED_DEPTH_BUFFER
	
		return depth * ( far - near ) - far;
	#else
		return depth * ( near - far ) - near;
	#endif
}
float viewZToPerspectiveDepth( const in float viewZ, const in float near, const in float far ) {
	return ( ( near + viewZ ) * far ) / ( ( far - near ) * viewZ );
}
float perspectiveDepthToViewZ( const in float depth, const in float near, const in float far ) {
	
	#ifdef USE_REVERSED_DEPTH_BUFFER
		return ( near * far ) / ( ( near - far ) * depth - near );
	#else
		return ( near * far ) / ( ( far - near ) * depth - far );
	#endif
}`,cT=`#ifdef PREMULTIPLIED_ALPHA
	gl_FragColor.rgb *= gl_FragColor.a;
#endif`,uT=`vec4 mvPosition = vec4( transformed, 1.0 );
#ifdef USE_BATCHING
	mvPosition = batchingMatrix * mvPosition;
#endif
#ifdef USE_INSTANCING
	mvPosition = instanceMatrix * mvPosition;
#endif
mvPosition = modelViewMatrix * mvPosition;
gl_Position = projectionMatrix * mvPosition;`,fT=`#ifdef DITHERING
	gl_FragColor.rgb = dithering( gl_FragColor.rgb );
#endif`,dT=`#ifdef DITHERING
	vec3 dithering( vec3 color ) {
		float grid_position = rand( gl_FragCoord.xy );
		vec3 dither_shift_RGB = vec3( 0.25 / 255.0, -0.25 / 255.0, 0.25 / 255.0 );
		dither_shift_RGB = mix( 2.0 * dither_shift_RGB, -2.0 * dither_shift_RGB, grid_position );
		return color + dither_shift_RGB;
	}
#endif`,hT=`float roughnessFactor = roughness;
#ifdef USE_ROUGHNESSMAP
	vec4 texelRoughness = texture2D( roughnessMap, vRoughnessMapUv );
	roughnessFactor *= texelRoughness.g;
#endif`,pT=`#ifdef USE_ROUGHNESSMAP
	uniform sampler2D roughnessMap;
#endif`,mT=`#if NUM_SPOT_LIGHT_COORDS > 0
	varying vec4 vSpotLightCoord[ NUM_SPOT_LIGHT_COORDS ];
#endif
#if NUM_SPOT_LIGHT_MAPS > 0
	uniform sampler2D spotLightMap[ NUM_SPOT_LIGHT_MAPS ];
#endif
#ifdef USE_SHADOWMAP
	#if NUM_DIR_LIGHT_SHADOWS > 0
		#if defined( SHADOWMAP_TYPE_PCF )
			uniform sampler2DShadow directionalShadowMap[ NUM_DIR_LIGHT_SHADOWS ];
		#else
			uniform sampler2D directionalShadowMap[ NUM_DIR_LIGHT_SHADOWS ];
		#endif
		varying vec4 vDirectionalShadowCoord[ NUM_DIR_LIGHT_SHADOWS ];
		struct DirectionalLightShadow {
			float shadowIntensity;
			float shadowBias;
			float shadowNormalBias;
			float shadowRadius;
			vec2 shadowMapSize;
		};
		uniform DirectionalLightShadow directionalLightShadows[ NUM_DIR_LIGHT_SHADOWS ];
	#endif
	#if NUM_SPOT_LIGHT_SHADOWS > 0
		#if defined( SHADOWMAP_TYPE_PCF )
			uniform sampler2DShadow spotShadowMap[ NUM_SPOT_LIGHT_SHADOWS ];
		#else
			uniform sampler2D spotShadowMap[ NUM_SPOT_LIGHT_SHADOWS ];
		#endif
		struct SpotLightShadow {
			float shadowIntensity;
			float shadowBias;
			float shadowNormalBias;
			float shadowRadius;
			vec2 shadowMapSize;
		};
		uniform SpotLightShadow spotLightShadows[ NUM_SPOT_LIGHT_SHADOWS ];
	#endif
	#if NUM_POINT_LIGHT_SHADOWS > 0
		#if defined( SHADOWMAP_TYPE_PCF )
			uniform samplerCubeShadow pointShadowMap[ NUM_POINT_LIGHT_SHADOWS ];
		#elif defined( SHADOWMAP_TYPE_BASIC )
			uniform samplerCube pointShadowMap[ NUM_POINT_LIGHT_SHADOWS ];
		#endif
		varying vec4 vPointShadowCoord[ NUM_POINT_LIGHT_SHADOWS ];
		struct PointLightShadow {
			float shadowIntensity;
			float shadowBias;
			float shadowNormalBias;
			float shadowRadius;
			vec2 shadowMapSize;
			float shadowCameraNear;
			float shadowCameraFar;
		};
		uniform PointLightShadow pointLightShadows[ NUM_POINT_LIGHT_SHADOWS ];
	#endif
	#if defined( SHADOWMAP_TYPE_PCF )
		float interleavedGradientNoise( vec2 position ) {
			return fract( 52.9829189 * fract( dot( position, vec2( 0.06711056, 0.00583715 ) ) ) );
		}
		vec2 vogelDiskSample( int sampleIndex, int samplesCount, float phi ) {
			const float goldenAngle = 2.399963229728653;
			float r = sqrt( ( float( sampleIndex ) + 0.5 ) / float( samplesCount ) );
			float theta = float( sampleIndex ) * goldenAngle + phi;
			return vec2( cos( theta ), sin( theta ) ) * r;
		}
	#endif
	#if defined( SHADOWMAP_TYPE_PCF )
		float getShadow( sampler2DShadow shadowMap, vec2 shadowMapSize, float shadowIntensity, float shadowBias, float shadowRadius, vec4 shadowCoord ) {
			float shadow = 1.0;
			shadowCoord.xyz /= shadowCoord.w;
			shadowCoord.z += shadowBias;
			bool inFrustum = shadowCoord.x >= 0.0 && shadowCoord.x <= 1.0 && shadowCoord.y >= 0.0 && shadowCoord.y <= 1.0;
			bool frustumTest = inFrustum && shadowCoord.z <= 1.0;
			if ( frustumTest ) {
				vec2 texelSize = vec2( 1.0 ) / shadowMapSize;
				float radius = shadowRadius * texelSize.x;
				float phi = interleavedGradientNoise( gl_FragCoord.xy ) * PI2;
				shadow = (
					texture( shadowMap, vec3( shadowCoord.xy + vogelDiskSample( 0, 5, phi ) * radius, shadowCoord.z ) ) +
					texture( shadowMap, vec3( shadowCoord.xy + vogelDiskSample( 1, 5, phi ) * radius, shadowCoord.z ) ) +
					texture( shadowMap, vec3( shadowCoord.xy + vogelDiskSample( 2, 5, phi ) * radius, shadowCoord.z ) ) +
					texture( shadowMap, vec3( shadowCoord.xy + vogelDiskSample( 3, 5, phi ) * radius, shadowCoord.z ) ) +
					texture( shadowMap, vec3( shadowCoord.xy + vogelDiskSample( 4, 5, phi ) * radius, shadowCoord.z ) )
				) * 0.2;
			}
			return mix( 1.0, shadow, shadowIntensity );
		}
	#elif defined( SHADOWMAP_TYPE_VSM )
		float getShadow( sampler2D shadowMap, vec2 shadowMapSize, float shadowIntensity, float shadowBias, float shadowRadius, vec4 shadowCoord ) {
			float shadow = 1.0;
			shadowCoord.xyz /= shadowCoord.w;
			#ifdef USE_REVERSED_DEPTH_BUFFER
				shadowCoord.z -= shadowBias;
			#else
				shadowCoord.z += shadowBias;
			#endif
			bool inFrustum = shadowCoord.x >= 0.0 && shadowCoord.x <= 1.0 && shadowCoord.y >= 0.0 && shadowCoord.y <= 1.0;
			bool frustumTest = inFrustum && shadowCoord.z <= 1.0;
			if ( frustumTest ) {
				vec2 distribution = texture2D( shadowMap, shadowCoord.xy ).rg;
				float mean = distribution.x;
				float variance = distribution.y * distribution.y;
				#ifdef USE_REVERSED_DEPTH_BUFFER
					float hard_shadow = step( mean, shadowCoord.z );
				#else
					float hard_shadow = step( shadowCoord.z, mean );
				#endif
				
				if ( hard_shadow == 1.0 ) {
					shadow = 1.0;
				} else {
					variance = max( variance, 0.0000001 );
					float d = shadowCoord.z - mean;
					float p_max = variance / ( variance + d * d );
					p_max = clamp( ( p_max - 0.3 ) / 0.65, 0.0, 1.0 );
					shadow = max( hard_shadow, p_max );
				}
			}
			return mix( 1.0, shadow, shadowIntensity );
		}
	#else
		float getShadow( sampler2D shadowMap, vec2 shadowMapSize, float shadowIntensity, float shadowBias, float shadowRadius, vec4 shadowCoord ) {
			float shadow = 1.0;
			shadowCoord.xyz /= shadowCoord.w;
			#ifdef USE_REVERSED_DEPTH_BUFFER
				shadowCoord.z -= shadowBias;
			#else
				shadowCoord.z += shadowBias;
			#endif
			bool inFrustum = shadowCoord.x >= 0.0 && shadowCoord.x <= 1.0 && shadowCoord.y >= 0.0 && shadowCoord.y <= 1.0;
			bool frustumTest = inFrustum && shadowCoord.z <= 1.0;
			if ( frustumTest ) {
				float depth = texture2D( shadowMap, shadowCoord.xy ).r;
				#ifdef USE_REVERSED_DEPTH_BUFFER
					shadow = step( depth, shadowCoord.z );
				#else
					shadow = step( shadowCoord.z, depth );
				#endif
			}
			return mix( 1.0, shadow, shadowIntensity );
		}
	#endif
	#if NUM_POINT_LIGHT_SHADOWS > 0
	#if defined( SHADOWMAP_TYPE_PCF )
	float getPointShadow( samplerCubeShadow shadowMap, vec2 shadowMapSize, float shadowIntensity, float shadowBias, float shadowRadius, vec4 shadowCoord, float shadowCameraNear, float shadowCameraFar ) {
		float shadow = 1.0;
		vec3 lightToPosition = shadowCoord.xyz;
		vec3 bd3D = normalize( lightToPosition );
		vec3 absVec = abs( lightToPosition );
		float viewSpaceZ = max( max( absVec.x, absVec.y ), absVec.z );
		if ( viewSpaceZ - shadowCameraFar <= 0.0 && viewSpaceZ - shadowCameraNear >= 0.0 ) {
			#ifdef USE_REVERSED_DEPTH_BUFFER
				float dp = ( shadowCameraNear * ( shadowCameraFar - viewSpaceZ ) ) / ( viewSpaceZ * ( shadowCameraFar - shadowCameraNear ) );
				dp -= shadowBias;
			#else
				float dp = ( shadowCameraFar * ( viewSpaceZ - shadowCameraNear ) ) / ( viewSpaceZ * ( shadowCameraFar - shadowCameraNear ) );
				dp += shadowBias;
			#endif
			float texelSize = shadowRadius / shadowMapSize.x;
			vec3 absDir = abs( bd3D );
			vec3 tangent = absDir.x > absDir.z ? vec3( 0.0, 1.0, 0.0 ) : vec3( 1.0, 0.0, 0.0 );
			tangent = normalize( cross( bd3D, tangent ) );
			vec3 bitangent = cross( bd3D, tangent );
			float phi = interleavedGradientNoise( gl_FragCoord.xy ) * PI2;
			vec2 sample0 = vogelDiskSample( 0, 5, phi );
			vec2 sample1 = vogelDiskSample( 1, 5, phi );
			vec2 sample2 = vogelDiskSample( 2, 5, phi );
			vec2 sample3 = vogelDiskSample( 3, 5, phi );
			vec2 sample4 = vogelDiskSample( 4, 5, phi );
			shadow = (
				texture( shadowMap, vec4( bd3D + ( tangent * sample0.x + bitangent * sample0.y ) * texelSize, dp ) ) +
				texture( shadowMap, vec4( bd3D + ( tangent * sample1.x + bitangent * sample1.y ) * texelSize, dp ) ) +
				texture( shadowMap, vec4( bd3D + ( tangent * sample2.x + bitangent * sample2.y ) * texelSize, dp ) ) +
				texture( shadowMap, vec4( bd3D + ( tangent * sample3.x + bitangent * sample3.y ) * texelSize, dp ) ) +
				texture( shadowMap, vec4( bd3D + ( tangent * sample4.x + bitangent * sample4.y ) * texelSize, dp ) )
			) * 0.2;
		}
		return mix( 1.0, shadow, shadowIntensity );
	}
	#elif defined( SHADOWMAP_TYPE_BASIC )
	float getPointShadow( samplerCube shadowMap, vec2 shadowMapSize, float shadowIntensity, float shadowBias, float shadowRadius, vec4 shadowCoord, float shadowCameraNear, float shadowCameraFar ) {
		float shadow = 1.0;
		vec3 lightToPosition = shadowCoord.xyz;
		vec3 absVec = abs( lightToPosition );
		float viewSpaceZ = max( max( absVec.x, absVec.y ), absVec.z );
		if ( viewSpaceZ - shadowCameraFar <= 0.0 && viewSpaceZ - shadowCameraNear >= 0.0 ) {
			float dp = ( shadowCameraFar * ( viewSpaceZ - shadowCameraNear ) ) / ( viewSpaceZ * ( shadowCameraFar - shadowCameraNear ) );
			dp += shadowBias;
			vec3 bd3D = normalize( lightToPosition );
			float depth = textureCube( shadowMap, bd3D ).r;
			#ifdef USE_REVERSED_DEPTH_BUFFER
				depth = 1.0 - depth;
			#endif
			shadow = step( dp, depth );
		}
		return mix( 1.0, shadow, shadowIntensity );
	}
	#endif
	#endif
#endif`,gT=`#if NUM_SPOT_LIGHT_COORDS > 0
	uniform mat4 spotLightMatrix[ NUM_SPOT_LIGHT_COORDS ];
	varying vec4 vSpotLightCoord[ NUM_SPOT_LIGHT_COORDS ];
#endif
#ifdef USE_SHADOWMAP
	#if NUM_DIR_LIGHT_SHADOWS > 0
		uniform mat4 directionalShadowMatrix[ NUM_DIR_LIGHT_SHADOWS ];
		varying vec4 vDirectionalShadowCoord[ NUM_DIR_LIGHT_SHADOWS ];
		struct DirectionalLightShadow {
			float shadowIntensity;
			float shadowBias;
			float shadowNormalBias;
			float shadowRadius;
			vec2 shadowMapSize;
		};
		uniform DirectionalLightShadow directionalLightShadows[ NUM_DIR_LIGHT_SHADOWS ];
	#endif
	#if NUM_SPOT_LIGHT_SHADOWS > 0
		struct SpotLightShadow {
			float shadowIntensity;
			float shadowBias;
			float shadowNormalBias;
			float shadowRadius;
			vec2 shadowMapSize;
		};
		uniform SpotLightShadow spotLightShadows[ NUM_SPOT_LIGHT_SHADOWS ];
	#endif
	#if NUM_POINT_LIGHT_SHADOWS > 0
		uniform mat4 pointShadowMatrix[ NUM_POINT_LIGHT_SHADOWS ];
		varying vec4 vPointShadowCoord[ NUM_POINT_LIGHT_SHADOWS ];
		struct PointLightShadow {
			float shadowIntensity;
			float shadowBias;
			float shadowNormalBias;
			float shadowRadius;
			vec2 shadowMapSize;
			float shadowCameraNear;
			float shadowCameraFar;
		};
		uniform PointLightShadow pointLightShadows[ NUM_POINT_LIGHT_SHADOWS ];
	#endif
#endif`,_T=`#if ( defined( USE_SHADOWMAP ) && ( NUM_DIR_LIGHT_SHADOWS > 0 || NUM_POINT_LIGHT_SHADOWS > 0 ) ) || ( NUM_SPOT_LIGHT_COORDS > 0 )
	#ifdef HAS_NORMAL
		vec3 shadowWorldNormal = transformNormalByInverseViewMatrix( transformedNormal, viewMatrix );
	#else
		vec3 shadowWorldNormal = vec3( 0.0 );
	#endif
	vec4 shadowWorldPosition;
#endif
#if defined( USE_SHADOWMAP )
	#if NUM_DIR_LIGHT_SHADOWS > 0
		#pragma unroll_loop_start
		for ( int i = 0; i < NUM_DIR_LIGHT_SHADOWS; i ++ ) {
			shadowWorldPosition = worldPosition + vec4( shadowWorldNormal * directionalLightShadows[ i ].shadowNormalBias, 0 );
			vDirectionalShadowCoord[ i ] = directionalShadowMatrix[ i ] * shadowWorldPosition;
		}
		#pragma unroll_loop_end
	#endif
	#if NUM_POINT_LIGHT_SHADOWS > 0
		#pragma unroll_loop_start
		for ( int i = 0; i < NUM_POINT_LIGHT_SHADOWS; i ++ ) {
			shadowWorldPosition = worldPosition + vec4( shadowWorldNormal * pointLightShadows[ i ].shadowNormalBias, 0 );
			vPointShadowCoord[ i ] = pointShadowMatrix[ i ] * shadowWorldPosition;
		}
		#pragma unroll_loop_end
	#endif
#endif
#if NUM_SPOT_LIGHT_COORDS > 0
	#pragma unroll_loop_start
	for ( int i = 0; i < NUM_SPOT_LIGHT_COORDS; i ++ ) {
		shadowWorldPosition = worldPosition;
		#if ( defined( USE_SHADOWMAP ) && UNROLLED_LOOP_INDEX < NUM_SPOT_LIGHT_SHADOWS )
			shadowWorldPosition.xyz += shadowWorldNormal * spotLightShadows[ i ].shadowNormalBias;
		#endif
		vSpotLightCoord[ i ] = spotLightMatrix[ i ] * shadowWorldPosition;
	}
	#pragma unroll_loop_end
#endif`,vT=`float getShadowMask() {
	float shadow = 1.0;
	#ifdef USE_SHADOWMAP
	#if NUM_DIR_LIGHT_SHADOWS > 0
	DirectionalLightShadow directionalLight;
	#pragma unroll_loop_start
	for ( int i = 0; i < NUM_DIR_LIGHT_SHADOWS; i ++ ) {
		directionalLight = directionalLightShadows[ i ];
		shadow *= receiveShadow ? getShadow( directionalShadowMap[ i ], directionalLight.shadowMapSize, directionalLight.shadowIntensity, directionalLight.shadowBias, directionalLight.shadowRadius, vDirectionalShadowCoord[ i ] ) : 1.0;
	}
	#pragma unroll_loop_end
	#endif
	#if NUM_SPOT_LIGHT_SHADOWS > 0
	SpotLightShadow spotLight;
	#pragma unroll_loop_start
	for ( int i = 0; i < NUM_SPOT_LIGHT_SHADOWS; i ++ ) {
		spotLight = spotLightShadows[ i ];
		shadow *= receiveShadow ? getShadow( spotShadowMap[ i ], spotLight.shadowMapSize, spotLight.shadowIntensity, spotLight.shadowBias, spotLight.shadowRadius, vSpotLightCoord[ i ] ) : 1.0;
	}
	#pragma unroll_loop_end
	#endif
	#if NUM_POINT_LIGHT_SHADOWS > 0 && ( defined( SHADOWMAP_TYPE_PCF ) || defined( SHADOWMAP_TYPE_BASIC ) )
	PointLightShadow pointLight;
	#pragma unroll_loop_start
	for ( int i = 0; i < NUM_POINT_LIGHT_SHADOWS; i ++ ) {
		pointLight = pointLightShadows[ i ];
		shadow *= receiveShadow ? getPointShadow( pointShadowMap[ i ], pointLight.shadowMapSize, pointLight.shadowIntensity, pointLight.shadowBias, pointLight.shadowRadius, vPointShadowCoord[ i ], pointLight.shadowCameraNear, pointLight.shadowCameraFar ) : 1.0;
	}
	#pragma unroll_loop_end
	#endif
	#endif
	return shadow;
}`,xT=`#ifdef USE_SKINNING
	mat4 boneMatX = getBoneMatrix( skinIndex.x );
	mat4 boneMatY = getBoneMatrix( skinIndex.y );
	mat4 boneMatZ = getBoneMatrix( skinIndex.z );
	mat4 boneMatW = getBoneMatrix( skinIndex.w );
#endif`,ST=`#ifdef USE_SKINNING
	uniform mat4 bindMatrix;
	uniform mat4 bindMatrixInverse;
	uniform highp sampler2D boneTexture;
	mat4 getBoneMatrix( const in float i ) {
		int size = textureSize( boneTexture, 0 ).x;
		int j = int( i ) * 4;
		int x = j % size;
		int y = j / size;
		vec4 v1 = texelFetch( boneTexture, ivec2( x, y ), 0 );
		vec4 v2 = texelFetch( boneTexture, ivec2( x + 1, y ), 0 );
		vec4 v3 = texelFetch( boneTexture, ivec2( x + 2, y ), 0 );
		vec4 v4 = texelFetch( boneTexture, ivec2( x + 3, y ), 0 );
		return mat4( v1, v2, v3, v4 );
	}
#endif`,MT=`#ifdef USE_SKINNING
	vec4 skinVertex = bindMatrix * vec4( transformed, 1.0 );
	vec4 skinned = vec4( 0.0 );
	skinned += boneMatX * skinVertex * skinWeight.x;
	skinned += boneMatY * skinVertex * skinWeight.y;
	skinned += boneMatZ * skinVertex * skinWeight.z;
	skinned += boneMatW * skinVertex * skinWeight.w;
	transformed = ( bindMatrixInverse * skinned ).xyz;
#endif`,yT=`#ifdef USE_SKINNING
	mat4 skinMatrix = mat4( 0.0 );
	skinMatrix += skinWeight.x * boneMatX;
	skinMatrix += skinWeight.y * boneMatY;
	skinMatrix += skinWeight.z * boneMatZ;
	skinMatrix += skinWeight.w * boneMatW;
	skinMatrix = bindMatrixInverse * skinMatrix * bindMatrix;
	objectNormal = vec4( skinMatrix * vec4( objectNormal, 0.0 ) ).xyz;
	#ifdef USE_TANGENT
		objectTangent = vec4( skinMatrix * vec4( objectTangent, 0.0 ) ).xyz;
	#endif
#endif`,ET=`float specularStrength;
#ifdef USE_SPECULARMAP
	vec4 texelSpecular = texture2D( specularMap, vSpecularMapUv );
	specularStrength = texelSpecular.r;
#else
	specularStrength = 1.0;
#endif`,bT=`#ifdef USE_SPECULARMAP
	uniform sampler2D specularMap;
#endif`,TT=`#if defined( TONE_MAPPING )
	gl_FragColor.rgb = toneMapping( gl_FragColor.rgb );
#endif`,AT=`#ifndef saturate
#define saturate( a ) clamp( a, 0.0, 1.0 )
#endif
uniform float toneMappingExposure;
vec3 LinearToneMapping( vec3 color ) {
	return saturate( toneMappingExposure * color );
}
vec3 ReinhardToneMapping( vec3 color ) {
	color *= toneMappingExposure;
	return saturate( color / ( vec3( 1.0 ) + color ) );
}
vec3 CineonToneMapping( vec3 color ) {
	color *= toneMappingExposure;
	color = max( vec3( 0.0 ), color - 0.004 );
	return pow( ( color * ( 6.2 * color + 0.5 ) ) / ( color * ( 6.2 * color + 1.7 ) + 0.06 ), vec3( 2.2 ) );
}
vec3 RRTAndODTFit( vec3 v ) {
	vec3 a = v * ( v + 0.0245786 ) - 0.000090537;
	vec3 b = v * ( 0.983729 * v + 0.4329510 ) + 0.238081;
	return a / b;
}
vec3 ACESFilmicToneMapping( vec3 color ) {
	const mat3 ACESInputMat = mat3(
		vec3( 0.59719, 0.07600, 0.02840 ),		vec3( 0.35458, 0.90834, 0.13383 ),
		vec3( 0.04823, 0.01566, 0.83777 )
	);
	const mat3 ACESOutputMat = mat3(
		vec3(  1.60475, -0.10208, -0.00327 ),		vec3( -0.53108,  1.10813, -0.07276 ),
		vec3( -0.07367, -0.00605,  1.07602 )
	);
	color *= toneMappingExposure / 0.6;
	color = ACESInputMat * color;
	color = RRTAndODTFit( color );
	color = ACESOutputMat * color;
	return saturate( color );
}
const mat3 LINEAR_REC2020_TO_LINEAR_SRGB = mat3(
	vec3( 1.6605, - 0.1246, - 0.0182 ),
	vec3( - 0.5876, 1.1329, - 0.1006 ),
	vec3( - 0.0728, - 0.0083, 1.1187 )
);
const mat3 LINEAR_SRGB_TO_LINEAR_REC2020 = mat3(
	vec3( 0.6274, 0.0691, 0.0164 ),
	vec3( 0.3293, 0.9195, 0.0880 ),
	vec3( 0.0433, 0.0113, 0.8956 )
);
vec3 agxDefaultContrastApprox( vec3 x ) {
	vec3 x2 = x * x;
	vec3 x4 = x2 * x2;
	return + 15.5 * x4 * x2
		- 40.14 * x4 * x
		+ 31.96 * x4
		- 6.868 * x2 * x
		+ 0.4298 * x2
		+ 0.1191 * x
		- 0.00232;
}
vec3 AgXToneMapping( vec3 color ) {
	const mat3 AgXInsetMatrix = mat3(
		vec3( 0.856627153315983, 0.137318972929847, 0.11189821299995 ),
		vec3( 0.0951212405381588, 0.761241990602591, 0.0767994186031903 ),
		vec3( 0.0482516061458583, 0.101439036467562, 0.811302368396859 )
	);
	const mat3 AgXOutsetMatrix = mat3(
		vec3( 1.1271005818144368, - 0.1413297634984383, - 0.14132976349843826 ),
		vec3( - 0.11060664309660323, 1.157823702216272, - 0.11060664309660294 ),
		vec3( - 0.016493938717834573, - 0.016493938717834257, 1.2519364065950405 )
	);
	const float AgxMinEv = - 12.47393;	const float AgxMaxEv = 4.026069;
	color *= toneMappingExposure;
	color = LINEAR_SRGB_TO_LINEAR_REC2020 * color;
	color = AgXInsetMatrix * color;
	color = max( color, 1e-10 );	color = log2( color );
	color = ( color - AgxMinEv ) / ( AgxMaxEv - AgxMinEv );
	color = clamp( color, 0.0, 1.0 );
	color = agxDefaultContrastApprox( color );
	color = AgXOutsetMatrix * color;
	color = pow( max( vec3( 0.0 ), color ), vec3( 2.2 ) );
	color = LINEAR_REC2020_TO_LINEAR_SRGB * color;
	color = clamp( color, 0.0, 1.0 );
	return color;
}
vec3 NeutralToneMapping( vec3 color ) {
	const float StartCompression = 0.8 - 0.04;
	const float Desaturation = 0.15;
	color *= toneMappingExposure;
	float x = min( color.r, min( color.g, color.b ) );
	float offset = x < 0.08 ? x - 6.25 * x * x : 0.04;
	color -= offset;
	float peak = max( color.r, max( color.g, color.b ) );
	if ( peak < StartCompression ) return color;
	float d = 1. - StartCompression;
	float newPeak = 1. - d * d / ( peak + d - StartCompression );
	color *= newPeak / peak;
	float g = 1. - 1. / ( Desaturation * ( peak - newPeak ) + 1. );
	return mix( color, vec3( newPeak ), g );
}
vec3 CustomToneMapping( vec3 color ) { return color; }`,RT=`#ifdef USE_TRANSMISSION
	material.transmission = transmission;
	material.transmissionAlpha = 1.0;
	material.thickness = thickness;
	material.attenuationDistance = attenuationDistance;
	material.attenuationColor = attenuationColor;
	#ifdef USE_TRANSMISSIONMAP
		material.transmission *= texture2D( transmissionMap, vTransmissionMapUv ).r;
	#endif
	#ifdef USE_THICKNESSMAP
		material.thickness *= texture2D( thicknessMap, vThicknessMapUv ).g;
	#endif
	vec3 pos = vWorldPosition;
	vec3 v = normalize( cameraPosition - pos );
	vec3 n = transformNormalByInverseViewMatrix( normal, viewMatrix );
	vec4 transmitted = getIBLVolumeRefraction(
		n, v, material.roughness, material.diffuseContribution, material.specularColorBlended, material.specularF90,
		pos, modelMatrix, viewMatrix, projectionMatrix, material.dispersion, material.ior, material.thickness,
		material.attenuationColor, material.attenuationDistance );
	material.transmissionAlpha = mix( material.transmissionAlpha, transmitted.a, material.transmission );
	totalDiffuse = mix( totalDiffuse, transmitted.rgb, material.transmission );
#endif`,CT=`#ifdef USE_TRANSMISSION
	uniform float transmission;
	uniform float thickness;
	uniform float attenuationDistance;
	uniform vec3 attenuationColor;
	#ifdef USE_TRANSMISSIONMAP
		uniform sampler2D transmissionMap;
	#endif
	#ifdef USE_THICKNESSMAP
		uniform sampler2D thicknessMap;
	#endif
	uniform vec2 transmissionSamplerSize;
	uniform sampler2D transmissionSamplerMap;
	uniform mat4 modelMatrix;
	uniform mat4 projectionMatrix;
	varying vec3 vWorldPosition;
	float w0( float a ) {
		return ( 1.0 / 6.0 ) * ( a * ( a * ( - a + 3.0 ) - 3.0 ) + 1.0 );
	}
	float w1( float a ) {
		return ( 1.0 / 6.0 ) * ( a *  a * ( 3.0 * a - 6.0 ) + 4.0 );
	}
	float w2( float a ){
		return ( 1.0 / 6.0 ) * ( a * ( a * ( - 3.0 * a + 3.0 ) + 3.0 ) + 1.0 );
	}
	float w3( float a ) {
		return ( 1.0 / 6.0 ) * ( a * a * a );
	}
	float g0( float a ) {
		return w0( a ) + w1( a );
	}
	float g1( float a ) {
		return w2( a ) + w3( a );
	}
	float h0( float a ) {
		return - 1.0 + w1( a ) / ( w0( a ) + w1( a ) );
	}
	float h1( float a ) {
		return 1.0 + w3( a ) / ( w2( a ) + w3( a ) );
	}
	vec4 bicubic( sampler2D tex, vec2 uv, vec4 texelSize, float lod ) {
		uv = uv * texelSize.zw + 0.5;
		vec2 iuv = floor( uv );
		vec2 fuv = fract( uv );
		float g0x = g0( fuv.x );
		float g1x = g1( fuv.x );
		float h0x = h0( fuv.x );
		float h1x = h1( fuv.x );
		float h0y = h0( fuv.y );
		float h1y = h1( fuv.y );
		vec2 p0 = ( vec2( iuv.x + h0x, iuv.y + h0y ) - 0.5 ) * texelSize.xy;
		vec2 p1 = ( vec2( iuv.x + h1x, iuv.y + h0y ) - 0.5 ) * texelSize.xy;
		vec2 p2 = ( vec2( iuv.x + h0x, iuv.y + h1y ) - 0.5 ) * texelSize.xy;
		vec2 p3 = ( vec2( iuv.x + h1x, iuv.y + h1y ) - 0.5 ) * texelSize.xy;
		return g0( fuv.y ) * ( g0x * textureLod( tex, p0, lod ) + g1x * textureLod( tex, p1, lod ) ) +
			g1( fuv.y ) * ( g0x * textureLod( tex, p2, lod ) + g1x * textureLod( tex, p3, lod ) );
	}
	vec4 textureBicubic( sampler2D sampler, vec2 uv, float lod ) {
		vec2 fLodSize = vec2( textureSize( sampler, int( lod ) ) );
		vec2 cLodSize = vec2( textureSize( sampler, int( lod + 1.0 ) ) );
		vec2 fLodSizeInv = 1.0 / fLodSize;
		vec2 cLodSizeInv = 1.0 / cLodSize;
		vec4 fSample = bicubic( sampler, uv, vec4( fLodSizeInv, fLodSize ), floor( lod ) );
		vec4 cSample = bicubic( sampler, uv, vec4( cLodSizeInv, cLodSize ), ceil( lod ) );
		return mix( fSample, cSample, fract( lod ) );
	}
	vec3 getVolumeTransmissionRay( const in vec3 n, const in vec3 v, const in float thickness, const in float ior, const in mat4 modelMatrix ) {
		vec3 refractionVector = refract( - v, normalize( n ), 1.0 / ior );
		vec3 modelScale;
		modelScale.x = length( vec3( modelMatrix[ 0 ].xyz ) );
		modelScale.y = length( vec3( modelMatrix[ 1 ].xyz ) );
		modelScale.z = length( vec3( modelMatrix[ 2 ].xyz ) );
		return normalize( refractionVector ) * thickness * modelScale;
	}
	float applyIorToRoughness( const in float roughness, const in float ior ) {
		return roughness * clamp( ior * 2.0 - 2.0, 0.0, 1.0 );
	}
	vec4 getTransmissionSample( const in vec2 fragCoord, const in float roughness, const in float ior ) {
		float lod = log2( transmissionSamplerSize.x ) * applyIorToRoughness( roughness, ior );
		return textureBicubic( transmissionSamplerMap, fragCoord.xy, lod );
	}
	vec3 volumeAttenuation( const in float transmissionDistance, const in vec3 attenuationColor, const in float attenuationDistance ) {
		if ( isinf( attenuationDistance ) ) {
			return vec3( 1.0 );
		} else {
			vec3 attenuationCoefficient = -log( attenuationColor ) / attenuationDistance;
			vec3 transmittance = exp( - attenuationCoefficient * transmissionDistance );			return transmittance;
		}
	}
	vec4 getIBLVolumeRefraction( const in vec3 n, const in vec3 v, const in float roughness, const in vec3 diffuseColor,
		const in vec3 specularColor, const in float specularF90, const in vec3 position, const in mat4 modelMatrix,
		const in mat4 viewMatrix, const in mat4 projMatrix, const in float dispersion, const in float ior, const in float thickness,
		const in vec3 attenuationColor, const in float attenuationDistance ) {
		vec4 transmittedLight;
		vec3 transmittance;
		#ifdef USE_DISPERSION
			float halfSpread = ( ior - 1.0 ) * 0.025 * dispersion;
			vec3 iors = vec3( ior - halfSpread, ior, ior + halfSpread );
			for ( int i = 0; i < 3; i ++ ) {
				vec3 transmissionRay = getVolumeTransmissionRay( n, v, thickness, iors[ i ], modelMatrix );
				vec3 refractedRayExit = position + transmissionRay;
				vec4 ndcPos = projMatrix * viewMatrix * vec4( refractedRayExit, 1.0 );
				vec2 refractionCoords = ndcPos.xy / ndcPos.w;
				refractionCoords += 1.0;
				refractionCoords /= 2.0;
				vec4 transmissionSample = getTransmissionSample( refractionCoords, roughness, iors[ i ] );
				transmittedLight[ i ] = transmissionSample[ i ];
				transmittedLight.a += transmissionSample.a;
				transmittance[ i ] = diffuseColor[ i ] * volumeAttenuation( length( transmissionRay ), attenuationColor, attenuationDistance )[ i ];
			}
			transmittedLight.a /= 3.0;
		#else
			vec3 transmissionRay = getVolumeTransmissionRay( n, v, thickness, ior, modelMatrix );
			vec3 refractedRayExit = position + transmissionRay;
			vec4 ndcPos = projMatrix * viewMatrix * vec4( refractedRayExit, 1.0 );
			vec2 refractionCoords = ndcPos.xy / ndcPos.w;
			refractionCoords += 1.0;
			refractionCoords /= 2.0;
			transmittedLight = getTransmissionSample( refractionCoords, roughness, ior );
			transmittance = diffuseColor * volumeAttenuation( length( transmissionRay ), attenuationColor, attenuationDistance );
		#endif
		vec3 attenuatedColor = transmittance * transmittedLight.rgb;
		vec3 F = EnvironmentBRDF( n, v, specularColor, specularF90, roughness );
		float transmittanceFactor = ( transmittance.r + transmittance.g + transmittance.b ) / 3.0;
		return vec4( ( 1.0 - F ) * attenuatedColor, 1.0 - ( 1.0 - transmittedLight.a ) * transmittanceFactor );
	}
#endif`,wT=`#if defined( USE_UV ) || defined( USE_ANISOTROPY )
	varying vec2 vUv;
#endif
#ifdef USE_MAP
	varying vec2 vMapUv;
#endif
#ifdef USE_ALPHAMAP
	varying vec2 vAlphaMapUv;
#endif
#ifdef USE_LIGHTMAP
	varying vec2 vLightMapUv;
#endif
#ifdef USE_AOMAP
	varying vec2 vAoMapUv;
#endif
#ifdef USE_BUMPMAP
	varying vec2 vBumpMapUv;
#endif
#ifdef USE_NORMALMAP
	varying vec2 vNormalMapUv;
#endif
#ifdef USE_EMISSIVEMAP
	varying vec2 vEmissiveMapUv;
#endif
#ifdef USE_METALNESSMAP
	varying vec2 vMetalnessMapUv;
#endif
#ifdef USE_ROUGHNESSMAP
	varying vec2 vRoughnessMapUv;
#endif
#ifdef USE_ANISOTROPYMAP
	varying vec2 vAnisotropyMapUv;
#endif
#ifdef USE_CLEARCOATMAP
	varying vec2 vClearcoatMapUv;
#endif
#ifdef USE_CLEARCOAT_NORMALMAP
	varying vec2 vClearcoatNormalMapUv;
#endif
#ifdef USE_CLEARCOAT_ROUGHNESSMAP
	varying vec2 vClearcoatRoughnessMapUv;
#endif
#ifdef USE_IRIDESCENCEMAP
	varying vec2 vIridescenceMapUv;
#endif
#ifdef USE_IRIDESCENCE_THICKNESSMAP
	varying vec2 vIridescenceThicknessMapUv;
#endif
#ifdef USE_SHEEN_COLORMAP
	varying vec2 vSheenColorMapUv;
#endif
#ifdef USE_SHEEN_ROUGHNESSMAP
	varying vec2 vSheenRoughnessMapUv;
#endif
#ifdef USE_SPECULARMAP
	varying vec2 vSpecularMapUv;
#endif
#ifdef USE_SPECULAR_COLORMAP
	varying vec2 vSpecularColorMapUv;
#endif
#ifdef USE_SPECULAR_INTENSITYMAP
	varying vec2 vSpecularIntensityMapUv;
#endif
#ifdef USE_TRANSMISSIONMAP
	uniform mat3 transmissionMapTransform;
	varying vec2 vTransmissionMapUv;
#endif
#ifdef USE_THICKNESSMAP
	uniform mat3 thicknessMapTransform;
	varying vec2 vThicknessMapUv;
#endif`,DT=`#if defined( USE_UV ) || defined( USE_ANISOTROPY )
	varying vec2 vUv;
#endif
#ifdef USE_MAP
	uniform mat3 mapTransform;
	varying vec2 vMapUv;
#endif
#ifdef USE_ALPHAMAP
	uniform mat3 alphaMapTransform;
	varying vec2 vAlphaMapUv;
#endif
#ifdef USE_LIGHTMAP
	uniform mat3 lightMapTransform;
	varying vec2 vLightMapUv;
#endif
#ifdef USE_AOMAP
	uniform mat3 aoMapTransform;
	varying vec2 vAoMapUv;
#endif
#ifdef USE_BUMPMAP
	uniform mat3 bumpMapTransform;
	varying vec2 vBumpMapUv;
#endif
#ifdef USE_NORMALMAP
	uniform mat3 normalMapTransform;
	varying vec2 vNormalMapUv;
#endif
#ifdef USE_DISPLACEMENTMAP
	uniform mat3 displacementMapTransform;
	varying vec2 vDisplacementMapUv;
#endif
#ifdef USE_EMISSIVEMAP
	uniform mat3 emissiveMapTransform;
	varying vec2 vEmissiveMapUv;
#endif
#ifdef USE_METALNESSMAP
	uniform mat3 metalnessMapTransform;
	varying vec2 vMetalnessMapUv;
#endif
#ifdef USE_ROUGHNESSMAP
	uniform mat3 roughnessMapTransform;
	varying vec2 vRoughnessMapUv;
#endif
#ifdef USE_ANISOTROPYMAP
	uniform mat3 anisotropyMapTransform;
	varying vec2 vAnisotropyMapUv;
#endif
#ifdef USE_CLEARCOATMAP
	uniform mat3 clearcoatMapTransform;
	varying vec2 vClearcoatMapUv;
#endif
#ifdef USE_CLEARCOAT_NORMALMAP
	uniform mat3 clearcoatNormalMapTransform;
	varying vec2 vClearcoatNormalMapUv;
#endif
#ifdef USE_CLEARCOAT_ROUGHNESSMAP
	uniform mat3 clearcoatRoughnessMapTransform;
	varying vec2 vClearcoatRoughnessMapUv;
#endif
#ifdef USE_SHEEN_COLORMAP
	uniform mat3 sheenColorMapTransform;
	varying vec2 vSheenColorMapUv;
#endif
#ifdef USE_SHEEN_ROUGHNESSMAP
	uniform mat3 sheenRoughnessMapTransform;
	varying vec2 vSheenRoughnessMapUv;
#endif
#ifdef USE_IRIDESCENCEMAP
	uniform mat3 iridescenceMapTransform;
	varying vec2 vIridescenceMapUv;
#endif
#ifdef USE_IRIDESCENCE_THICKNESSMAP
	uniform mat3 iridescenceThicknessMapTransform;
	varying vec2 vIridescenceThicknessMapUv;
#endif
#ifdef USE_SPECULARMAP
	uniform mat3 specularMapTransform;
	varying vec2 vSpecularMapUv;
#endif
#ifdef USE_SPECULAR_COLORMAP
	uniform mat3 specularColorMapTransform;
	varying vec2 vSpecularColorMapUv;
#endif
#ifdef USE_SPECULAR_INTENSITYMAP
	uniform mat3 specularIntensityMapTransform;
	varying vec2 vSpecularIntensityMapUv;
#endif
#ifdef USE_TRANSMISSIONMAP
	uniform mat3 transmissionMapTransform;
	varying vec2 vTransmissionMapUv;
#endif
#ifdef USE_THICKNESSMAP
	uniform mat3 thicknessMapTransform;
	varying vec2 vThicknessMapUv;
#endif`,UT=`#if defined( USE_UV ) || defined( USE_ANISOTROPY )
	vUv = vec3( uv, 1 ).xy;
#endif
#ifdef USE_MAP
	vMapUv = ( mapTransform * vec3( MAP_UV, 1 ) ).xy;
#endif
#ifdef USE_ALPHAMAP
	vAlphaMapUv = ( alphaMapTransform * vec3( ALPHAMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_LIGHTMAP
	vLightMapUv = ( lightMapTransform * vec3( LIGHTMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_AOMAP
	vAoMapUv = ( aoMapTransform * vec3( AOMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_BUMPMAP
	vBumpMapUv = ( bumpMapTransform * vec3( BUMPMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_NORMALMAP
	vNormalMapUv = ( normalMapTransform * vec3( NORMALMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_DISPLACEMENTMAP
	vDisplacementMapUv = ( displacementMapTransform * vec3( DISPLACEMENTMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_EMISSIVEMAP
	vEmissiveMapUv = ( emissiveMapTransform * vec3( EMISSIVEMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_METALNESSMAP
	vMetalnessMapUv = ( metalnessMapTransform * vec3( METALNESSMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_ROUGHNESSMAP
	vRoughnessMapUv = ( roughnessMapTransform * vec3( ROUGHNESSMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_ANISOTROPYMAP
	vAnisotropyMapUv = ( anisotropyMapTransform * vec3( ANISOTROPYMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_CLEARCOATMAP
	vClearcoatMapUv = ( clearcoatMapTransform * vec3( CLEARCOATMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_CLEARCOAT_NORMALMAP
	vClearcoatNormalMapUv = ( clearcoatNormalMapTransform * vec3( CLEARCOAT_NORMALMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_CLEARCOAT_ROUGHNESSMAP
	vClearcoatRoughnessMapUv = ( clearcoatRoughnessMapTransform * vec3( CLEARCOAT_ROUGHNESSMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_IRIDESCENCEMAP
	vIridescenceMapUv = ( iridescenceMapTransform * vec3( IRIDESCENCEMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_IRIDESCENCE_THICKNESSMAP
	vIridescenceThicknessMapUv = ( iridescenceThicknessMapTransform * vec3( IRIDESCENCE_THICKNESSMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_SHEEN_COLORMAP
	vSheenColorMapUv = ( sheenColorMapTransform * vec3( SHEEN_COLORMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_SHEEN_ROUGHNESSMAP
	vSheenRoughnessMapUv = ( sheenRoughnessMapTransform * vec3( SHEEN_ROUGHNESSMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_SPECULARMAP
	vSpecularMapUv = ( specularMapTransform * vec3( SPECULARMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_SPECULAR_COLORMAP
	vSpecularColorMapUv = ( specularColorMapTransform * vec3( SPECULAR_COLORMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_SPECULAR_INTENSITYMAP
	vSpecularIntensityMapUv = ( specularIntensityMapTransform * vec3( SPECULAR_INTENSITYMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_TRANSMISSIONMAP
	vTransmissionMapUv = ( transmissionMapTransform * vec3( TRANSMISSIONMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_THICKNESSMAP
	vThicknessMapUv = ( thicknessMapTransform * vec3( THICKNESSMAP_UV, 1 ) ).xy;
#endif`,LT=`#if defined( USE_ENVMAP ) || defined( DISTANCE ) || defined ( USE_SHADOWMAP ) || defined ( USE_TRANSMISSION ) || NUM_SPOT_LIGHT_COORDS > 0
	vec4 worldPosition = vec4( transformed, 1.0 );
	#ifdef USE_BATCHING
		worldPosition = batchingMatrix * worldPosition;
	#endif
	#ifdef USE_INSTANCING
		worldPosition = instanceMatrix * worldPosition;
	#endif
	worldPosition = modelMatrix * worldPosition;
#endif`;const NT=`varying vec2 vUv;
uniform mat3 uvTransform;
void main() {
	vUv = ( uvTransform * vec3( uv, 1 ) ).xy;
	gl_Position = vec4( position.xy, 1.0, 1.0 );
}`,OT=`uniform sampler2D t2D;
uniform float backgroundIntensity;
varying vec2 vUv;
void main() {
	vec4 texColor = texture2D( t2D, vUv );
	#ifdef DECODE_VIDEO_TEXTURE
		texColor = vec4( mix( pow( texColor.rgb * 0.9478672986 + vec3( 0.0521327014 ), vec3( 2.4 ) ), texColor.rgb * 0.0773993808, vec3( lessThanEqual( texColor.rgb, vec3( 0.04045 ) ) ) ), texColor.w );
	#endif
	texColor.rgb *= backgroundIntensity;
	gl_FragColor = texColor;
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
}`,PT=`varying vec3 vWorldDirection;
#include <common>
void main() {
	vWorldDirection = transformDirection( position, modelMatrix );
	#include <begin_vertex>
	#include <project_vertex>
	gl_Position.z = gl_Position.w;
}`,zT=`#ifdef ENVMAP_TYPE_CUBE
	uniform samplerCube envMap;
#elif defined( ENVMAP_TYPE_CUBE_UV )
	uniform sampler2D envMap;
#endif
uniform float backgroundBlurriness;
uniform float backgroundIntensity;
uniform mat3 backgroundRotation;
varying vec3 vWorldDirection;
#include <cube_uv_reflection_fragment>
void main() {
	#ifdef ENVMAP_TYPE_CUBE
		vec4 texColor = textureCube( envMap, backgroundRotation * vWorldDirection );
	#elif defined( ENVMAP_TYPE_CUBE_UV )
		vec4 texColor = textureCubeUV( envMap, backgroundRotation * vWorldDirection, backgroundBlurriness );
	#else
		vec4 texColor = vec4( 0.0, 0.0, 0.0, 1.0 );
	#endif
	texColor.rgb *= backgroundIntensity;
	gl_FragColor = texColor;
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
}`,FT=`varying vec3 vWorldDirection;
#include <common>
void main() {
	vWorldDirection = transformDirection( position, modelMatrix );
	#include <begin_vertex>
	#include <project_vertex>
	gl_Position.z = gl_Position.w;
}`,BT=`uniform samplerCube tCube;
uniform float tFlip;
uniform float opacity;
varying vec3 vWorldDirection;
void main() {
	vec4 texColor = textureCube( tCube, vec3( tFlip * vWorldDirection.x, vWorldDirection.yz ) );
	gl_FragColor = texColor;
	gl_FragColor.a *= opacity;
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
}`,IT=`#include <common>
#include <batching_pars_vertex>
#include <uv_pars_vertex>
#include <displacementmap_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
varying vec2 vHighPrecisionZW;
void main() {
	#include <uv_vertex>
	#include <batching_vertex>
	#include <skinbase_vertex>
	#include <morphinstance_vertex>
	#ifdef USE_DISPLACEMENTMAP
		#include <beginnormal_vertex>
		#include <morphnormal_vertex>
		#include <skinnormal_vertex>
	#endif
	#include <begin_vertex>
	#include <morphtarget_vertex>
	#include <skinning_vertex>
	#include <displacementmap_vertex>
	#include <project_vertex>
	#include <logdepthbuf_vertex>
	#include <clipping_planes_vertex>
	vHighPrecisionZW = gl_Position.zw;
}`,HT=`#if DEPTH_PACKING == 3200
	uniform float opacity;
#endif
#include <common>
#include <packing>
#include <uv_pars_fragment>
#include <map_pars_fragment>
#include <alphamap_pars_fragment>
#include <alphatest_pars_fragment>
#include <alphahash_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
varying vec2 vHighPrecisionZW;
void main() {
	vec4 diffuseColor = vec4( 1.0 );
	#include <clipping_planes_fragment>
	#if DEPTH_PACKING == 3200
		diffuseColor.a = opacity;
	#endif
	#include <map_fragment>
	#include <alphamap_fragment>
	#include <alphatest_fragment>
	#include <alphahash_fragment>
	#include <logdepthbuf_fragment>
	#ifdef USE_REVERSED_DEPTH_BUFFER
		float fragCoordZ = vHighPrecisionZW[ 0 ] / vHighPrecisionZW[ 1 ];
	#else
		float fragCoordZ = 0.5 * vHighPrecisionZW[ 0 ] / vHighPrecisionZW[ 1 ] + 0.5;
	#endif
	#if DEPTH_PACKING == 3200
		gl_FragColor = vec4( vec3( 1.0 - fragCoordZ ), opacity );
	#elif DEPTH_PACKING == 3201
		gl_FragColor = packDepthToRGBA( fragCoordZ );
	#elif DEPTH_PACKING == 3202
		gl_FragColor = vec4( packDepthToRGB( fragCoordZ ), 1.0 );
	#elif DEPTH_PACKING == 3203
		gl_FragColor = vec4( packDepthToRG( fragCoordZ ), 0.0, 1.0 );
	#endif
}`,GT=`#define DISTANCE
varying vec3 vWorldPosition;
#include <common>
#include <batching_pars_vertex>
#include <uv_pars_vertex>
#include <displacementmap_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <clipping_planes_pars_vertex>
void main() {
	#include <uv_vertex>
	#include <batching_vertex>
	#include <skinbase_vertex>
	#include <morphinstance_vertex>
	#ifdef USE_DISPLACEMENTMAP
		#include <beginnormal_vertex>
		#include <morphnormal_vertex>
		#include <skinnormal_vertex>
	#endif
	#include <begin_vertex>
	#include <morphtarget_vertex>
	#include <skinning_vertex>
	#include <displacementmap_vertex>
	#include <project_vertex>
	#include <worldpos_vertex>
	#include <clipping_planes_vertex>
	vWorldPosition = worldPosition.xyz;
}`,VT=`#define DISTANCE
uniform vec3 referencePosition;
uniform float nearDistance;
uniform float farDistance;
varying vec3 vWorldPosition;
#include <common>
#include <uv_pars_fragment>
#include <map_pars_fragment>
#include <alphamap_pars_fragment>
#include <alphatest_pars_fragment>
#include <alphahash_pars_fragment>
#include <clipping_planes_pars_fragment>
void main() {
	vec4 diffuseColor = vec4( 1.0 );
	#include <clipping_planes_fragment>
	#include <map_fragment>
	#include <alphamap_fragment>
	#include <alphatest_fragment>
	#include <alphahash_fragment>
	float dist = length( vWorldPosition - referencePosition );
	dist = ( dist - nearDistance ) / ( farDistance - nearDistance );
	dist = saturate( dist );
	gl_FragColor = vec4( dist, 0.0, 0.0, 1.0 );
}`,kT=`varying vec3 vWorldDirection;
#include <common>
void main() {
	vWorldDirection = transformDirection( position, modelMatrix );
	#include <begin_vertex>
	#include <project_vertex>
}`,XT=`uniform sampler2D tEquirect;
varying vec3 vWorldDirection;
#include <common>
void main() {
	vec3 direction = normalize( vWorldDirection );
	vec2 sampleUV = equirectUv( direction );
	gl_FragColor = texture2D( tEquirect, sampleUV );
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
}`,WT=`uniform float scale;
attribute float lineDistance;
varying float vLineDistance;
#include <common>
#include <uv_pars_vertex>
#include <color_pars_vertex>
#include <fog_pars_vertex>
#include <morphtarget_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
void main() {
	vLineDistance = scale * lineDistance;
	#include <uv_vertex>
	#include <color_vertex>
	#include <morphinstance_vertex>
	#include <morphcolor_vertex>
	#include <begin_vertex>
	#include <morphtarget_vertex>
	#include <project_vertex>
	#include <logdepthbuf_vertex>
	#include <clipping_planes_vertex>
	#include <fog_vertex>
}`,qT=`uniform vec3 diffuse;
uniform float opacity;
uniform float dashSize;
uniform float totalSize;
varying float vLineDistance;
#include <common>
#include <color_pars_fragment>
#include <uv_pars_fragment>
#include <map_pars_fragment>
#include <fog_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
void main() {
	vec4 diffuseColor = vec4( diffuse, opacity );
	#include <clipping_planes_fragment>
	if ( mod( vLineDistance, totalSize ) > dashSize ) {
		discard;
	}
	vec3 outgoingLight = vec3( 0.0 );
	#include <logdepthbuf_fragment>
	#include <map_fragment>
	#include <color_fragment>
	outgoingLight = diffuseColor.rgb;
	#include <opaque_fragment>
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
	#include <fog_fragment>
	#include <premultiplied_alpha_fragment>
}`,YT=`#include <common>
#include <batching_pars_vertex>
#include <uv_pars_vertex>
#include <envmap_pars_vertex>
#include <color_pars_vertex>
#include <fog_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
void main() {
	#include <uv_vertex>
	#include <color_vertex>
	#include <morphinstance_vertex>
	#include <morphcolor_vertex>
	#include <batching_vertex>
	#if defined ( USE_ENVMAP ) || defined ( USE_SKINNING )
		#include <beginnormal_vertex>
		#include <morphnormal_vertex>
		#include <skinbase_vertex>
		#include <skinnormal_vertex>
		#include <defaultnormal_vertex>
	#endif
	#include <begin_vertex>
	#include <morphtarget_vertex>
	#include <skinning_vertex>
	#include <project_vertex>
	#include <logdepthbuf_vertex>
	#include <clipping_planes_vertex>
	#include <worldpos_vertex>
	#include <envmap_vertex>
	#include <fog_vertex>
}`,ZT=`uniform vec3 diffuse;
uniform float opacity;
#ifndef FLAT_SHADED
	varying vec3 vNormal;
#endif
#include <common>
#include <dithering_pars_fragment>
#include <color_pars_fragment>
#include <uv_pars_fragment>
#include <map_pars_fragment>
#include <alphamap_pars_fragment>
#include <alphatest_pars_fragment>
#include <alphahash_pars_fragment>
#include <aomap_pars_fragment>
#include <lightmap_pars_fragment>
#include <envmap_common_pars_fragment>
#include <envmap_pars_fragment>
#include <fog_pars_fragment>
#include <specularmap_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
void main() {
	vec4 diffuseColor = vec4( diffuse, opacity );
	#include <clipping_planes_fragment>
	#include <logdepthbuf_fragment>
	#include <map_fragment>
	#include <color_fragment>
	#include <alphamap_fragment>
	#include <alphatest_fragment>
	#include <alphahash_fragment>
	#include <specularmap_fragment>
	ReflectedLight reflectedLight = ReflectedLight( vec3( 0.0 ), vec3( 0.0 ), vec3( 0.0 ), vec3( 0.0 ) );
	#ifdef USE_LIGHTMAP
		vec4 lightMapTexel = texture2D( lightMap, vLightMapUv );
		reflectedLight.indirectDiffuse += lightMapTexel.rgb * lightMapIntensity * RECIPROCAL_PI;
	#else
		reflectedLight.indirectDiffuse += vec3( 1.0 );
	#endif
	#include <aomap_fragment>
	reflectedLight.indirectDiffuse *= diffuseColor.rgb;
	vec3 outgoingLight = reflectedLight.indirectDiffuse;
	#include <envmap_fragment>
	#include <opaque_fragment>
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
	#include <fog_fragment>
	#include <premultiplied_alpha_fragment>
	#include <dithering_fragment>
}`,KT=`#define LAMBERT
varying vec3 vViewPosition;
#include <common>
#include <batching_pars_vertex>
#include <uv_pars_vertex>
#include <displacementmap_pars_vertex>
#include <envmap_pars_vertex>
#include <color_pars_vertex>
#include <fog_pars_vertex>
#include <normal_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <shadowmap_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
void main() {
	#include <uv_vertex>
	#include <color_vertex>
	#include <morphinstance_vertex>
	#include <morphcolor_vertex>
	#include <batching_vertex>
	#include <beginnormal_vertex>
	#include <morphnormal_vertex>
	#include <skinbase_vertex>
	#include <skinnormal_vertex>
	#include <defaultnormal_vertex>
	#include <normal_vertex>
	#include <begin_vertex>
	#include <morphtarget_vertex>
	#include <skinning_vertex>
	#include <displacementmap_vertex>
	#include <project_vertex>
	#include <logdepthbuf_vertex>
	#include <clipping_planes_vertex>
	vViewPosition = - mvPosition.xyz;
	#include <worldpos_vertex>
	#include <envmap_vertex>
	#include <shadowmap_vertex>
	#include <fog_vertex>
}`,QT=`#define LAMBERT
uniform vec3 diffuse;
uniform vec3 emissive;
uniform float opacity;
#include <common>
#include <dithering_pars_fragment>
#include <color_pars_fragment>
#include <uv_pars_fragment>
#include <map_pars_fragment>
#include <alphamap_pars_fragment>
#include <alphatest_pars_fragment>
#include <alphahash_pars_fragment>
#include <aomap_pars_fragment>
#include <lightmap_pars_fragment>
#include <emissivemap_pars_fragment>
#include <cube_uv_reflection_fragment>
#include <envmap_common_pars_fragment>
#include <envmap_pars_fragment>
#include <envmap_physical_pars_fragment>
#include <fog_pars_fragment>
#include <bsdfs>
#include <lights_pars_begin>
#include <normal_pars_fragment>
#include <lights_lambert_pars_fragment>
#include <shadowmap_pars_fragment>
#include <bumpmap_pars_fragment>
#include <normalmap_pars_fragment>
#include <specularmap_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
void main() {
	vec4 diffuseColor = vec4( diffuse, opacity );
	#include <clipping_planes_fragment>
	ReflectedLight reflectedLight = ReflectedLight( vec3( 0.0 ), vec3( 0.0 ), vec3( 0.0 ), vec3( 0.0 ) );
	vec3 totalEmissiveRadiance = emissive;
	#include <logdepthbuf_fragment>
	#include <map_fragment>
	#include <color_fragment>
	#include <alphamap_fragment>
	#include <alphatest_fragment>
	#include <alphahash_fragment>
	#include <specularmap_fragment>
	#include <normal_fragment_begin>
	#include <normal_fragment_maps>
	#include <emissivemap_fragment>
	#include <lights_lambert_fragment>
	#include <lights_fragment_begin>
	#include <lights_fragment_maps>
	#include <lights_fragment_end>
	#include <aomap_fragment>
	vec3 outgoingLight = reflectedLight.directDiffuse + reflectedLight.indirectDiffuse + totalEmissiveRadiance;
	#include <envmap_fragment>
	#include <opaque_fragment>
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
	#include <fog_fragment>
	#include <premultiplied_alpha_fragment>
	#include <dithering_fragment>
}`,JT=`#define MATCAP
varying vec3 vViewPosition;
#include <common>
#include <batching_pars_vertex>
#include <uv_pars_vertex>
#include <color_pars_vertex>
#include <displacementmap_pars_vertex>
#include <fog_pars_vertex>
#include <normal_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
void main() {
	#include <uv_vertex>
	#include <color_vertex>
	#include <morphinstance_vertex>
	#include <morphcolor_vertex>
	#include <batching_vertex>
	#include <beginnormal_vertex>
	#include <morphnormal_vertex>
	#include <skinbase_vertex>
	#include <skinnormal_vertex>
	#include <defaultnormal_vertex>
	#include <normal_vertex>
	#include <begin_vertex>
	#include <morphtarget_vertex>
	#include <skinning_vertex>
	#include <displacementmap_vertex>
	#include <project_vertex>
	#include <logdepthbuf_vertex>
	#include <clipping_planes_vertex>
	#include <fog_vertex>
	vViewPosition = - mvPosition.xyz;
}`,$T=`#define MATCAP
uniform vec3 diffuse;
uniform float opacity;
uniform sampler2D matcap;
varying vec3 vViewPosition;
#include <common>
#include <dithering_pars_fragment>
#include <color_pars_fragment>
#include <uv_pars_fragment>
#include <map_pars_fragment>
#include <alphamap_pars_fragment>
#include <alphatest_pars_fragment>
#include <alphahash_pars_fragment>
#include <fog_pars_fragment>
#include <normal_pars_fragment>
#include <bumpmap_pars_fragment>
#include <normalmap_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
void main() {
	vec4 diffuseColor = vec4( diffuse, opacity );
	#include <clipping_planes_fragment>
	#include <logdepthbuf_fragment>
	#include <map_fragment>
	#include <color_fragment>
	#include <alphamap_fragment>
	#include <alphatest_fragment>
	#include <alphahash_fragment>
	#include <normal_fragment_begin>
	#include <normal_fragment_maps>
	vec3 viewDir = normalize( vViewPosition );
	vec3 x = normalize( vec3( viewDir.z, 0.0, - viewDir.x ) );
	vec3 y = cross( viewDir, x );
	vec2 uv = vec2( dot( x, normal ), dot( y, normal ) ) * 0.495 + 0.5;
	#ifdef USE_MATCAP
		vec4 matcapColor = texture2D( matcap, uv );
	#else
		vec4 matcapColor = vec4( vec3( mix( 0.2, 0.8, uv.y ) ), 1.0 );
	#endif
	vec3 outgoingLight = diffuseColor.rgb * matcapColor.rgb;
	#include <opaque_fragment>
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
	#include <fog_fragment>
	#include <premultiplied_alpha_fragment>
	#include <dithering_fragment>
}`,jT=`#define NORMAL
#if defined( FLAT_SHADED ) || defined( USE_BUMPMAP ) || defined( USE_NORMALMAP_TANGENTSPACE )
	varying vec3 vViewPosition;
#endif
#include <common>
#include <batching_pars_vertex>
#include <uv_pars_vertex>
#include <displacementmap_pars_vertex>
#include <normal_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
void main() {
	#include <uv_vertex>
	#include <batching_vertex>
	#include <beginnormal_vertex>
	#include <morphinstance_vertex>
	#include <morphnormal_vertex>
	#include <skinbase_vertex>
	#include <skinnormal_vertex>
	#include <defaultnormal_vertex>
	#include <normal_vertex>
	#include <begin_vertex>
	#include <morphtarget_vertex>
	#include <skinning_vertex>
	#include <displacementmap_vertex>
	#include <project_vertex>
	#include <logdepthbuf_vertex>
	#include <clipping_planes_vertex>
#if defined( FLAT_SHADED ) || defined( USE_BUMPMAP ) || defined( USE_NORMALMAP_TANGENTSPACE )
	vViewPosition = - mvPosition.xyz;
#endif
}`,e1=`#define NORMAL
uniform float opacity;
#if defined( FLAT_SHADED ) || defined( USE_BUMPMAP ) || defined( USE_NORMALMAP_TANGENTSPACE )
	varying vec3 vViewPosition;
#endif
#include <uv_pars_fragment>
#include <normal_pars_fragment>
#include <bumpmap_pars_fragment>
#include <normalmap_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
void main() {
	vec4 diffuseColor = vec4( 0.0, 0.0, 0.0, opacity );
	#include <clipping_planes_fragment>
	#include <logdepthbuf_fragment>
	#include <normal_fragment_begin>
	#include <normal_fragment_maps>
	gl_FragColor = vec4( normalize( normal ) * 0.5 + 0.5, diffuseColor.a );
	#ifdef OPAQUE
		gl_FragColor.a = 1.0;
	#endif
}`,t1=`#define PHONG
varying vec3 vViewPosition;
#include <common>
#include <batching_pars_vertex>
#include <uv_pars_vertex>
#include <displacementmap_pars_vertex>
#include <envmap_pars_vertex>
#include <color_pars_vertex>
#include <fog_pars_vertex>
#include <normal_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <shadowmap_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
void main() {
	#include <uv_vertex>
	#include <color_vertex>
	#include <morphcolor_vertex>
	#include <batching_vertex>
	#include <beginnormal_vertex>
	#include <morphinstance_vertex>
	#include <morphnormal_vertex>
	#include <skinbase_vertex>
	#include <skinnormal_vertex>
	#include <defaultnormal_vertex>
	#include <normal_vertex>
	#include <begin_vertex>
	#include <morphtarget_vertex>
	#include <skinning_vertex>
	#include <displacementmap_vertex>
	#include <project_vertex>
	#include <logdepthbuf_vertex>
	#include <clipping_planes_vertex>
	vViewPosition = - mvPosition.xyz;
	#include <worldpos_vertex>
	#include <envmap_vertex>
	#include <shadowmap_vertex>
	#include <fog_vertex>
}`,n1=`#define PHONG
uniform vec3 diffuse;
uniform vec3 emissive;
uniform vec3 specular;
uniform float shininess;
uniform float opacity;
#include <common>
#include <dithering_pars_fragment>
#include <color_pars_fragment>
#include <uv_pars_fragment>
#include <map_pars_fragment>
#include <alphamap_pars_fragment>
#include <alphatest_pars_fragment>
#include <alphahash_pars_fragment>
#include <aomap_pars_fragment>
#include <lightmap_pars_fragment>
#include <emissivemap_pars_fragment>
#include <cube_uv_reflection_fragment>
#include <envmap_common_pars_fragment>
#include <envmap_pars_fragment>
#include <envmap_physical_pars_fragment>
#include <fog_pars_fragment>
#include <bsdfs>
#include <lights_pars_begin>
#include <normal_pars_fragment>
#include <lights_phong_pars_fragment>
#include <shadowmap_pars_fragment>
#include <bumpmap_pars_fragment>
#include <normalmap_pars_fragment>
#include <specularmap_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
void main() {
	vec4 diffuseColor = vec4( diffuse, opacity );
	#include <clipping_planes_fragment>
	ReflectedLight reflectedLight = ReflectedLight( vec3( 0.0 ), vec3( 0.0 ), vec3( 0.0 ), vec3( 0.0 ) );
	vec3 totalEmissiveRadiance = emissive;
	#include <logdepthbuf_fragment>
	#include <map_fragment>
	#include <color_fragment>
	#include <alphamap_fragment>
	#include <alphatest_fragment>
	#include <alphahash_fragment>
	#include <specularmap_fragment>
	#include <normal_fragment_begin>
	#include <normal_fragment_maps>
	#include <emissivemap_fragment>
	#include <lights_phong_fragment>
	#include <lights_fragment_begin>
	#include <lights_fragment_maps>
	#include <lights_fragment_end>
	#include <aomap_fragment>
	vec3 outgoingLight = reflectedLight.directDiffuse + reflectedLight.indirectDiffuse + reflectedLight.directSpecular + reflectedLight.indirectSpecular + totalEmissiveRadiance;
	#include <envmap_fragment>
	#include <opaque_fragment>
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
	#include <fog_fragment>
	#include <premultiplied_alpha_fragment>
	#include <dithering_fragment>
}`,i1=`#define STANDARD
varying vec3 vViewPosition;
#ifdef USE_TRANSMISSION
	varying vec3 vWorldPosition;
#endif
#include <common>
#include <batching_pars_vertex>
#include <uv_pars_vertex>
#include <displacementmap_pars_vertex>
#include <color_pars_vertex>
#include <fog_pars_vertex>
#include <normal_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <shadowmap_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
void main() {
	#include <uv_vertex>
	#include <color_vertex>
	#include <morphinstance_vertex>
	#include <morphcolor_vertex>
	#include <batching_vertex>
	#include <beginnormal_vertex>
	#include <morphnormal_vertex>
	#include <skinbase_vertex>
	#include <skinnormal_vertex>
	#include <defaultnormal_vertex>
	#include <normal_vertex>
	#include <begin_vertex>
	#include <morphtarget_vertex>
	#include <skinning_vertex>
	#include <displacementmap_vertex>
	#include <project_vertex>
	#include <logdepthbuf_vertex>
	#include <clipping_planes_vertex>
	vViewPosition = - mvPosition.xyz;
	#include <worldpos_vertex>
	#include <shadowmap_vertex>
	#include <fog_vertex>
#ifdef USE_TRANSMISSION
	vWorldPosition = worldPosition.xyz;
#endif
}`,a1=`#define STANDARD
#ifdef PHYSICAL
	#define IOR
	#define USE_SPECULAR
#endif
uniform vec3 diffuse;
uniform vec3 emissive;
uniform float roughness;
uniform float metalness;
uniform float opacity;
#ifdef IOR
	uniform float ior;
#endif
#ifdef USE_SPECULAR
	uniform float specularIntensity;
	uniform vec3 specularColor;
	#ifdef USE_SPECULAR_COLORMAP
		uniform sampler2D specularColorMap;
	#endif
	#ifdef USE_SPECULAR_INTENSITYMAP
		uniform sampler2D specularIntensityMap;
	#endif
#endif
#ifdef USE_CLEARCOAT
	uniform float clearcoat;
	uniform float clearcoatRoughness;
#endif
#ifdef USE_DISPERSION
	uniform float dispersion;
#endif
#ifdef USE_IRIDESCENCE
	uniform float iridescence;
	uniform float iridescenceIOR;
	uniform float iridescenceThicknessMinimum;
	uniform float iridescenceThicknessMaximum;
#endif
#ifdef USE_SHEEN
	uniform vec3 sheenColor;
	uniform float sheenRoughness;
	#ifdef USE_SHEEN_COLORMAP
		uniform sampler2D sheenColorMap;
	#endif
	#ifdef USE_SHEEN_ROUGHNESSMAP
		uniform sampler2D sheenRoughnessMap;
	#endif
#endif
#ifdef USE_ANISOTROPY
	uniform vec2 anisotropyVector;
	#ifdef USE_ANISOTROPYMAP
		uniform sampler2D anisotropyMap;
	#endif
#endif
varying vec3 vViewPosition;
#include <common>
#include <dithering_pars_fragment>
#include <color_pars_fragment>
#include <uv_pars_fragment>
#include <map_pars_fragment>
#include <alphamap_pars_fragment>
#include <alphatest_pars_fragment>
#include <alphahash_pars_fragment>
#include <aomap_pars_fragment>
#include <lightmap_pars_fragment>
#include <emissivemap_pars_fragment>
#include <iridescence_fragment>
#include <cube_uv_reflection_fragment>
#include <envmap_common_pars_fragment>
#include <envmap_physical_pars_fragment>
#include <fog_pars_fragment>
#include <lights_pars_begin>
#include <normal_pars_fragment>
#include <lights_physical_pars_fragment>
#include <transmission_pars_fragment>
#include <shadowmap_pars_fragment>
#include <bumpmap_pars_fragment>
#include <normalmap_pars_fragment>
#include <clearcoat_pars_fragment>
#include <iridescence_pars_fragment>
#include <roughnessmap_pars_fragment>
#include <metalnessmap_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
void main() {
	vec4 diffuseColor = vec4( diffuse, opacity );
	#include <clipping_planes_fragment>
	ReflectedLight reflectedLight = ReflectedLight( vec3( 0.0 ), vec3( 0.0 ), vec3( 0.0 ), vec3( 0.0 ) );
	vec3 totalEmissiveRadiance = emissive;
	#include <logdepthbuf_fragment>
	#include <map_fragment>
	#include <color_fragment>
	#include <alphamap_fragment>
	#include <alphatest_fragment>
	#include <alphahash_fragment>
	#include <roughnessmap_fragment>
	#include <metalnessmap_fragment>
	#include <normal_fragment_begin>
	#include <normal_fragment_maps>
	#include <clearcoat_normal_fragment_begin>
	#include <clearcoat_normal_fragment_maps>
	#include <emissivemap_fragment>
	#include <lights_physical_fragment>
	#include <lights_fragment_begin>
	#include <lights_fragment_maps>
	#include <lights_fragment_end>
	#include <aomap_fragment>
	vec3 totalDiffuse = reflectedLight.directDiffuse + reflectedLight.indirectDiffuse;
	vec3 totalSpecular = reflectedLight.directSpecular + reflectedLight.indirectSpecular;
	#include <transmission_fragment>
	vec3 outgoingLight = totalDiffuse + totalSpecular + totalEmissiveRadiance;
	#ifdef USE_SHEEN
 
		outgoingLight = outgoingLight + sheenSpecularDirect + sheenSpecularIndirect;
 
 	#endif
	#ifdef USE_CLEARCOAT
		float dotNVcc = saturate( dot( geometryClearcoatNormal, geometryViewDir ) );
		vec3 Fcc = F_Schlick( material.clearcoatF0, material.clearcoatF90, dotNVcc );
		outgoingLight = outgoingLight * ( 1.0 - material.clearcoat * Fcc ) + ( clearcoatSpecularDirect + clearcoatSpecularIndirect ) * material.clearcoat;
	#endif
	#include <opaque_fragment>
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
	#include <fog_fragment>
	#include <premultiplied_alpha_fragment>
	#include <dithering_fragment>
}`,s1=`#define TOON
varying vec3 vViewPosition;
#include <common>
#include <batching_pars_vertex>
#include <uv_pars_vertex>
#include <displacementmap_pars_vertex>
#include <color_pars_vertex>
#include <fog_pars_vertex>
#include <normal_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <shadowmap_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
void main() {
	#include <uv_vertex>
	#include <color_vertex>
	#include <morphinstance_vertex>
	#include <morphcolor_vertex>
	#include <batching_vertex>
	#include <beginnormal_vertex>
	#include <morphnormal_vertex>
	#include <skinbase_vertex>
	#include <skinnormal_vertex>
	#include <defaultnormal_vertex>
	#include <normal_vertex>
	#include <begin_vertex>
	#include <morphtarget_vertex>
	#include <skinning_vertex>
	#include <displacementmap_vertex>
	#include <project_vertex>
	#include <logdepthbuf_vertex>
	#include <clipping_planes_vertex>
	vViewPosition = - mvPosition.xyz;
	#include <worldpos_vertex>
	#include <shadowmap_vertex>
	#include <fog_vertex>
}`,r1=`#define TOON
uniform vec3 diffuse;
uniform vec3 emissive;
uniform float opacity;
#include <common>
#include <dithering_pars_fragment>
#include <color_pars_fragment>
#include <uv_pars_fragment>
#include <map_pars_fragment>
#include <alphamap_pars_fragment>
#include <alphatest_pars_fragment>
#include <alphahash_pars_fragment>
#include <aomap_pars_fragment>
#include <lightmap_pars_fragment>
#include <emissivemap_pars_fragment>
#include <gradientmap_pars_fragment>
#include <fog_pars_fragment>
#include <bsdfs>
#include <lights_pars_begin>
#include <normal_pars_fragment>
#include <lights_toon_pars_fragment>
#include <shadowmap_pars_fragment>
#include <bumpmap_pars_fragment>
#include <normalmap_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
void main() {
	vec4 diffuseColor = vec4( diffuse, opacity );
	#include <clipping_planes_fragment>
	ReflectedLight reflectedLight = ReflectedLight( vec3( 0.0 ), vec3( 0.0 ), vec3( 0.0 ), vec3( 0.0 ) );
	vec3 totalEmissiveRadiance = emissive;
	#include <logdepthbuf_fragment>
	#include <map_fragment>
	#include <color_fragment>
	#include <alphamap_fragment>
	#include <alphatest_fragment>
	#include <alphahash_fragment>
	#include <normal_fragment_begin>
	#include <normal_fragment_maps>
	#include <emissivemap_fragment>
	#include <lights_toon_fragment>
	#include <lights_fragment_begin>
	#include <lights_fragment_maps>
	#include <lights_fragment_end>
	#include <aomap_fragment>
	vec3 outgoingLight = reflectedLight.directDiffuse + reflectedLight.indirectDiffuse + totalEmissiveRadiance;
	#include <opaque_fragment>
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
	#include <fog_fragment>
	#include <premultiplied_alpha_fragment>
	#include <dithering_fragment>
}`,o1=`uniform float size;
uniform float scale;
#include <common>
#include <color_pars_vertex>
#include <fog_pars_vertex>
#include <morphtarget_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
#ifdef USE_POINTS_UV
	varying vec2 vUv;
	uniform mat3 uvTransform;
#endif
void main() {
	#ifdef USE_POINTS_UV
		vUv = ( uvTransform * vec3( uv, 1 ) ).xy;
	#endif
	#include <color_vertex>
	#include <morphinstance_vertex>
	#include <morphcolor_vertex>
	#include <begin_vertex>
	#include <morphtarget_vertex>
	#include <project_vertex>
	gl_PointSize = size;
	#ifdef USE_SIZEATTENUATION
		bool isPerspective = isPerspectiveMatrix( projectionMatrix );
		if ( isPerspective ) gl_PointSize *= ( scale / - mvPosition.z );
	#endif
	#include <logdepthbuf_vertex>
	#include <clipping_planes_vertex>
	#include <worldpos_vertex>
	#include <fog_vertex>
}`,l1=`uniform vec3 diffuse;
uniform float opacity;
#include <common>
#include <color_pars_fragment>
#include <map_particle_pars_fragment>
#include <alphatest_pars_fragment>
#include <alphahash_pars_fragment>
#include <fog_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
void main() {
	vec4 diffuseColor = vec4( diffuse, opacity );
	#include <clipping_planes_fragment>
	vec3 outgoingLight = vec3( 0.0 );
	#include <logdepthbuf_fragment>
	#include <map_particle_fragment>
	#include <color_fragment>
	#include <alphatest_fragment>
	#include <alphahash_fragment>
	outgoingLight = diffuseColor.rgb;
	#include <opaque_fragment>
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
	#include <fog_fragment>
	#include <premultiplied_alpha_fragment>
}`,c1=`#include <common>
#include <batching_pars_vertex>
#include <fog_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <shadowmap_pars_vertex>
void main() {
	#include <batching_vertex>
	#include <beginnormal_vertex>
	#include <morphinstance_vertex>
	#include <morphnormal_vertex>
	#include <skinbase_vertex>
	#include <skinnormal_vertex>
	#include <defaultnormal_vertex>
	#include <begin_vertex>
	#include <morphtarget_vertex>
	#include <skinning_vertex>
	#include <project_vertex>
	#include <logdepthbuf_vertex>
	#include <worldpos_vertex>
	#include <shadowmap_vertex>
	#include <fog_vertex>
}`,u1=`uniform vec3 color;
uniform float opacity;
#include <common>
#include <fog_pars_fragment>
#include <bsdfs>
#include <lights_pars_begin>
#include <logdepthbuf_pars_fragment>
#include <shadowmap_pars_fragment>
#include <shadowmask_pars_fragment>
void main() {
	#include <logdepthbuf_fragment>
	gl_FragColor = vec4( color, opacity * ( 1.0 - getShadowMask() ) );
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
	#include <fog_fragment>
	#include <premultiplied_alpha_fragment>
}`,f1=`uniform float rotation;
uniform vec2 center;
#include <common>
#include <uv_pars_vertex>
#include <fog_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
void main() {
	#include <uv_vertex>
	vec4 mvPosition = modelViewMatrix[ 3 ];
	vec2 scale = vec2( length( modelMatrix[ 0 ].xyz ), length( modelMatrix[ 1 ].xyz ) );
	#ifndef USE_SIZEATTENUATION
		bool isPerspective = isPerspectiveMatrix( projectionMatrix );
		if ( isPerspective ) scale *= - mvPosition.z;
	#endif
	vec2 alignedPosition = ( position.xy - ( center - vec2( 0.5 ) ) ) * scale;
	vec2 rotatedPosition;
	rotatedPosition.x = cos( rotation ) * alignedPosition.x - sin( rotation ) * alignedPosition.y;
	rotatedPosition.y = sin( rotation ) * alignedPosition.x + cos( rotation ) * alignedPosition.y;
	mvPosition.xy += rotatedPosition;
	gl_Position = projectionMatrix * mvPosition;
	#include <logdepthbuf_vertex>
	#include <clipping_planes_vertex>
	#include <fog_vertex>
}`,d1=`uniform vec3 diffuse;
uniform float opacity;
#include <common>
#include <uv_pars_fragment>
#include <map_pars_fragment>
#include <alphamap_pars_fragment>
#include <alphatest_pars_fragment>
#include <alphahash_pars_fragment>
#include <fog_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
void main() {
	vec4 diffuseColor = vec4( diffuse, opacity );
	#include <clipping_planes_fragment>
	vec3 outgoingLight = vec3( 0.0 );
	#include <logdepthbuf_fragment>
	#include <map_fragment>
	#include <alphamap_fragment>
	#include <alphatest_fragment>
	#include <alphahash_fragment>
	outgoingLight = diffuseColor.rgb;
	#include <opaque_fragment>
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
	#include <fog_fragment>
}`,ct={alphahash_fragment:NE,alphahash_pars_fragment:OE,alphamap_fragment:PE,alphamap_pars_fragment:zE,alphatest_fragment:FE,alphatest_pars_fragment:BE,aomap_fragment:IE,aomap_pars_fragment:HE,batching_pars_vertex:GE,batching_vertex:VE,begin_vertex:kE,beginnormal_vertex:XE,bsdfs:WE,iridescence_fragment:qE,bumpmap_pars_fragment:YE,clipping_planes_fragment:ZE,clipping_planes_pars_fragment:KE,clipping_planes_pars_vertex:QE,clipping_planes_vertex:JE,color_fragment:$E,color_pars_fragment:jE,color_pars_vertex:eb,color_vertex:tb,common:nb,cube_uv_reflection_fragment:ib,defaultnormal_vertex:ab,displacementmap_pars_vertex:sb,displacementmap_vertex:rb,emissivemap_fragment:ob,emissivemap_pars_fragment:lb,colorspace_fragment:cb,colorspace_pars_fragment:ub,envmap_fragment:fb,envmap_common_pars_fragment:db,envmap_pars_fragment:hb,envmap_pars_vertex:pb,envmap_physical_pars_fragment:Tb,envmap_vertex:mb,fog_vertex:gb,fog_pars_vertex:_b,fog_fragment:vb,fog_pars_fragment:xb,gradientmap_pars_fragment:Sb,lightmap_pars_fragment:Mb,lights_lambert_fragment:yb,lights_lambert_pars_fragment:Eb,lights_pars_begin:bb,lights_toon_fragment:Ab,lights_toon_pars_fragment:Rb,lights_phong_fragment:Cb,lights_phong_pars_fragment:wb,lights_physical_fragment:Db,lights_physical_pars_fragment:Ub,lights_fragment_begin:Lb,lights_fragment_maps:Nb,lights_fragment_end:Ob,lightprobes_pars_fragment:Pb,logdepthbuf_fragment:zb,logdepthbuf_pars_fragment:Fb,logdepthbuf_pars_vertex:Bb,logdepthbuf_vertex:Ib,map_fragment:Hb,map_pars_fragment:Gb,map_particle_fragment:Vb,map_particle_pars_fragment:kb,metalnessmap_fragment:Xb,metalnessmap_pars_fragment:Wb,morphinstance_vertex:qb,morphcolor_vertex:Yb,morphnormal_vertex:Zb,morphtarget_pars_vertex:Kb,morphtarget_vertex:Qb,normal_fragment_begin:Jb,normal_fragment_maps:$b,normal_pars_fragment:jb,normal_pars_vertex:eT,normal_vertex:tT,normalmap_pars_fragment:nT,clearcoat_normal_fragment_begin:iT,clearcoat_normal_fragment_maps:aT,clearcoat_pars_fragment:sT,iridescence_pars_fragment:rT,opaque_fragment:oT,packing:lT,premultiplied_alpha_fragment:cT,project_vertex:uT,dithering_fragment:fT,dithering_pars_fragment:dT,roughnessmap_fragment:hT,roughnessmap_pars_fragment:pT,shadowmap_pars_fragment:mT,shadowmap_pars_vertex:gT,shadowmap_vertex:_T,shadowmask_pars_fragment:vT,skinbase_vertex:xT,skinning_pars_vertex:ST,skinning_vertex:MT,skinnormal_vertex:yT,specularmap_fragment:ET,specularmap_pars_fragment:bT,tonemapping_fragment:TT,tonemapping_pars_fragment:AT,transmission_fragment:RT,transmission_pars_fragment:CT,uv_pars_fragment:wT,uv_pars_vertex:DT,uv_vertex:UT,worldpos_vertex:LT,background_vert:NT,background_frag:OT,backgroundCube_vert:PT,backgroundCube_frag:zT,cube_vert:FT,cube_frag:BT,depth_vert:IT,depth_frag:HT,distance_vert:GT,distance_frag:VT,equirect_vert:kT,equirect_frag:XT,linedashed_vert:WT,linedashed_frag:qT,meshbasic_vert:YT,meshbasic_frag:ZT,meshlambert_vert:KT,meshlambert_frag:QT,meshmatcap_vert:JT,meshmatcap_frag:$T,meshnormal_vert:jT,meshnormal_frag:e1,meshphong_vert:t1,meshphong_frag:n1,meshphysical_vert:i1,meshphysical_frag:a1,meshtoon_vert:s1,meshtoon_frag:r1,points_vert:o1,points_frag:l1,shadow_vert:c1,shadow_frag:u1,sprite_vert:f1,sprite_frag:d1},Ne={common:{diffuse:{value:new Et(16777215)},opacity:{value:1},map:{value:null},mapTransform:{value:new st},alphaMap:{value:null},alphaMapTransform:{value:new st},alphaTest:{value:0}},specularmap:{specularMap:{value:null},specularMapTransform:{value:new st}},envmap:{envMap:{value:null},envMapRotation:{value:new st},reflectivity:{value:1},ior:{value:1.5},refractionRatio:{value:.98},dfgLUT:{value:null}},aomap:{aoMap:{value:null},aoMapIntensity:{value:1},aoMapTransform:{value:new st}},lightmap:{lightMap:{value:null},lightMapIntensity:{value:1},lightMapTransform:{value:new st}},bumpmap:{bumpMap:{value:null},bumpMapTransform:{value:new st},bumpScale:{value:1}},normalmap:{normalMap:{value:null},normalMapTransform:{value:new st},normalScale:{value:new Ut(1,1)}},displacementmap:{displacementMap:{value:null},displacementMapTransform:{value:new st},displacementScale:{value:1},displacementBias:{value:0}},emissivemap:{emissiveMap:{value:null},emissiveMapTransform:{value:new st}},metalnessmap:{metalnessMap:{value:null},metalnessMapTransform:{value:new st}},roughnessmap:{roughnessMap:{value:null},roughnessMapTransform:{value:new st}},gradientmap:{gradientMap:{value:null}},fog:{fogDensity:{value:25e-5},fogNear:{value:1},fogFar:{value:2e3},fogColor:{value:new Et(16777215)}},lights:{ambientLightColor:{value:[]},lightProbe:{value:[]},directionalLights:{value:[],properties:{direction:{},color:{}}},directionalLightShadows:{value:[],properties:{shadowIntensity:1,shadowBias:{},shadowNormalBias:{},shadowRadius:{},shadowMapSize:{}}},directionalShadowMatrix:{value:[]},spotLights:{value:[],properties:{color:{},position:{},direction:{},distance:{},coneCos:{},penumbraCos:{},decay:{}}},spotLightShadows:{value:[],properties:{shadowIntensity:1,shadowBias:{},shadowNormalBias:{},shadowRadius:{},shadowMapSize:{}}},spotLightMap:{value:[]},spotLightMatrix:{value:[]},pointLights:{value:[],properties:{color:{},position:{},decay:{},distance:{}}},pointLightShadows:{value:[],properties:{shadowIntensity:1,shadowBias:{},shadowNormalBias:{},shadowRadius:{},shadowMapSize:{},shadowCameraNear:{},shadowCameraFar:{}}},pointShadowMatrix:{value:[]},hemisphereLights:{value:[],properties:{direction:{},skyColor:{},groundColor:{}}},rectAreaLights:{value:[],properties:{color:{},position:{},width:{},height:{}}},ltc_1:{value:null},ltc_2:{value:null},probesSH:{value:null},probesMin:{value:new ae},probesMax:{value:new ae},probesResolution:{value:new ae}},points:{diffuse:{value:new Et(16777215)},opacity:{value:1},size:{value:1},scale:{value:1},map:{value:null},alphaMap:{value:null},alphaMapTransform:{value:new st},alphaTest:{value:0},uvTransform:{value:new st}},sprite:{diffuse:{value:new Et(16777215)},opacity:{value:1},center:{value:new Ut(.5,.5)},rotation:{value:0},map:{value:null},mapTransform:{value:new st},alphaMap:{value:null},alphaMapTransform:{value:new st},alphaTest:{value:0}}},Gi={basic:{uniforms:Fn([Ne.common,Ne.specularmap,Ne.envmap,Ne.aomap,Ne.lightmap,Ne.fog]),vertexShader:ct.meshbasic_vert,fragmentShader:ct.meshbasic_frag},lambert:{uniforms:Fn([Ne.common,Ne.specularmap,Ne.envmap,Ne.aomap,Ne.lightmap,Ne.emissivemap,Ne.bumpmap,Ne.normalmap,Ne.displacementmap,Ne.fog,Ne.lights,{emissive:{value:new Et(0)},envMapIntensity:{value:1}}]),vertexShader:ct.meshlambert_vert,fragmentShader:ct.meshlambert_frag},phong:{uniforms:Fn([Ne.common,Ne.specularmap,Ne.envmap,Ne.aomap,Ne.lightmap,Ne.emissivemap,Ne.bumpmap,Ne.normalmap,Ne.displacementmap,Ne.fog,Ne.lights,{emissive:{value:new Et(0)},specular:{value:new Et(1118481)},shininess:{value:30},envMapIntensity:{value:1}}]),vertexShader:ct.meshphong_vert,fragmentShader:ct.meshphong_frag},standard:{uniforms:Fn([Ne.common,Ne.envmap,Ne.aomap,Ne.lightmap,Ne.emissivemap,Ne.bumpmap,Ne.normalmap,Ne.displacementmap,Ne.roughnessmap,Ne.metalnessmap,Ne.fog,Ne.lights,{emissive:{value:new Et(0)},roughness:{value:1},metalness:{value:0},envMapIntensity:{value:1}}]),vertexShader:ct.meshphysical_vert,fragmentShader:ct.meshphysical_frag},toon:{uniforms:Fn([Ne.common,Ne.aomap,Ne.lightmap,Ne.emissivemap,Ne.bumpmap,Ne.normalmap,Ne.displacementmap,Ne.gradientmap,Ne.fog,Ne.lights,{emissive:{value:new Et(0)}}]),vertexShader:ct.meshtoon_vert,fragmentShader:ct.meshtoon_frag},matcap:{uniforms:Fn([Ne.common,Ne.bumpmap,Ne.normalmap,Ne.displacementmap,Ne.fog,{matcap:{value:null}}]),vertexShader:ct.meshmatcap_vert,fragmentShader:ct.meshmatcap_frag},points:{uniforms:Fn([Ne.points,Ne.fog]),vertexShader:ct.points_vert,fragmentShader:ct.points_frag},dashed:{uniforms:Fn([Ne.common,Ne.fog,{scale:{value:1},dashSize:{value:1},totalSize:{value:2}}]),vertexShader:ct.linedashed_vert,fragmentShader:ct.linedashed_frag},depth:{uniforms:Fn([Ne.common,Ne.displacementmap]),vertexShader:ct.depth_vert,fragmentShader:ct.depth_frag},normal:{uniforms:Fn([Ne.common,Ne.bumpmap,Ne.normalmap,Ne.displacementmap,{opacity:{value:1}}]),vertexShader:ct.meshnormal_vert,fragmentShader:ct.meshnormal_frag},sprite:{uniforms:Fn([Ne.sprite,Ne.fog]),vertexShader:ct.sprite_vert,fragmentShader:ct.sprite_frag},background:{uniforms:{uvTransform:{value:new st},t2D:{value:null},backgroundIntensity:{value:1}},vertexShader:ct.background_vert,fragmentShader:ct.background_frag},backgroundCube:{uniforms:{envMap:{value:null},backgroundBlurriness:{value:0},backgroundIntensity:{value:1},backgroundRotation:{value:new st}},vertexShader:ct.backgroundCube_vert,fragmentShader:ct.backgroundCube_frag},cube:{uniforms:{tCube:{value:null},tFlip:{value:-1},opacity:{value:1}},vertexShader:ct.cube_vert,fragmentShader:ct.cube_frag},equirect:{uniforms:{tEquirect:{value:null}},vertexShader:ct.equirect_vert,fragmentShader:ct.equirect_frag},distance:{uniforms:Fn([Ne.common,Ne.displacementmap,{referencePosition:{value:new ae},nearDistance:{value:1},farDistance:{value:1e3}}]),vertexShader:ct.distance_vert,fragmentShader:ct.distance_frag},shadow:{uniforms:Fn([Ne.lights,Ne.fog,{color:{value:new Et(0)},opacity:{value:1}}]),vertexShader:ct.shadow_vert,fragmentShader:ct.shadow_frag}};Gi.physical={uniforms:Fn([Gi.standard.uniforms,{clearcoat:{value:0},clearcoatMap:{value:null},clearcoatMapTransform:{value:new st},clearcoatNormalMap:{value:null},clearcoatNormalMapTransform:{value:new st},clearcoatNormalScale:{value:new Ut(1,1)},clearcoatRoughness:{value:0},clearcoatRoughnessMap:{value:null},clearcoatRoughnessMapTransform:{value:new st},dispersion:{value:0},iridescence:{value:0},iridescenceMap:{value:null},iridescenceMapTransform:{value:new st},iridescenceIOR:{value:1.3},iridescenceThicknessMinimum:{value:100},iridescenceThicknessMaximum:{value:400},iridescenceThicknessMap:{value:null},iridescenceThicknessMapTransform:{value:new st},sheen:{value:0},sheenColor:{value:new Et(0)},sheenColorMap:{value:null},sheenColorMapTransform:{value:new st},sheenRoughness:{value:1},sheenRoughnessMap:{value:null},sheenRoughnessMapTransform:{value:new st},transmission:{value:0},transmissionMap:{value:null},transmissionMapTransform:{value:new st},transmissionSamplerSize:{value:new Ut},transmissionSamplerMap:{value:null},thickness:{value:0},thicknessMap:{value:null},thicknessMapTransform:{value:new st},attenuationDistance:{value:0},attenuationColor:{value:new Et(0)},specularColor:{value:new Et(1,1,1)},specularColorMap:{value:null},specularColorMapTransform:{value:new st},specularIntensity:{value:1},specularIntensityMap:{value:null},specularIntensityMapTransform:{value:new st},anisotropyVector:{value:new Ut},anisotropyMap:{value:null},anisotropyMapTransform:{value:new st}}]),vertexShader:ct.meshphysical_vert,fragmentShader:ct.meshphysical_frag};const Ic={r:0,b:0,g:0},h1=new fn,Nv=new st;Nv.set(-1,0,0,0,1,0,0,0,1);function p1(s,e,i,r,l,u){const d=new Et(0);let h=l===!0?0:1,m,p,v=null,x=0,g=null;function E(z){let F=z.isScene===!0?z.background:null;if(F&&F.isTexture){const C=z.backgroundBlurriness>0;F=e.get(F,C)}return F}function T(z){let F=!1;const C=E(z);C===null?y(d,h):C&&C.isColor&&(y(C,1),F=!0);const O=s.xr.getEnvironmentBlendMode();O==="additive"?i.buffers.color.setClear(0,0,0,1,u):O==="alpha-blend"&&i.buffers.color.setClear(0,0,0,0,u),(s.autoClear||F)&&(i.buffers.depth.setTest(!0),i.buffers.depth.setMask(!0),i.buffers.color.setMask(!0),s.clear(s.autoClearColor,s.autoClearDepth,s.autoClearStencil))}function D(z,F){const C=E(F);C&&(C.isCubeTexture||C.mapping===eu)?(p===void 0&&(p=new Ui(new nl(1,1,1),new Yi({name:"BackgroundCubeMaterial",uniforms:kr(Gi.backgroundCube.uniforms),vertexShader:Gi.backgroundCube.vertexShader,fragmentShader:Gi.backgroundCube.fragmentShader,side:Yn,depthTest:!1,depthWrite:!1,fog:!1,allowOverride:!1})),p.geometry.deleteAttribute("normal"),p.geometry.deleteAttribute("uv"),p.onBeforeRender=function(O,U,B){this.matrixWorld.copyPosition(B.matrixWorld)},Object.defineProperty(p.material,"envMap",{get:function(){return this.uniforms.envMap.value}}),r.update(p)),p.material.uniforms.envMap.value=C,p.material.uniforms.backgroundBlurriness.value=F.backgroundBlurriness,p.material.uniforms.backgroundIntensity.value=F.backgroundIntensity,p.material.uniforms.backgroundRotation.value.setFromMatrix4(h1.makeRotationFromEuler(F.backgroundRotation)).transpose(),C.isCubeTexture&&C.isRenderTargetTexture===!1&&p.material.uniforms.backgroundRotation.value.premultiply(Nv),p.material.toneMapped=yt.getTransfer(C.colorSpace)!==It,(v!==C||x!==C.version||g!==s.toneMapping)&&(p.material.needsUpdate=!0,v=C,x=C.version,g=s.toneMapping),p.layers.enableAll(),z.unshift(p,p.geometry,p.material,0,0,null)):C&&C.isTexture&&(m===void 0&&(m=new Ui(new il(2,2),new Yi({name:"BackgroundMaterial",uniforms:kr(Gi.background.uniforms),vertexShader:Gi.background.vertexShader,fragmentShader:Gi.background.fragmentShader,side:ls,depthTest:!1,depthWrite:!1,fog:!1,allowOverride:!1})),m.geometry.deleteAttribute("normal"),Object.defineProperty(m.material,"map",{get:function(){return this.uniforms.t2D.value}}),r.update(m)),m.material.uniforms.t2D.value=C,m.material.uniforms.backgroundIntensity.value=F.backgroundIntensity,m.material.toneMapped=yt.getTransfer(C.colorSpace)!==It,C.matrixAutoUpdate===!0&&C.updateMatrix(),m.material.uniforms.uvTransform.value.copy(C.matrix),(v!==C||x!==C.version||g!==s.toneMapping)&&(m.material.needsUpdate=!0,v=C,x=C.version,g=s.toneMapping),m.layers.enableAll(),z.unshift(m,m.geometry,m.material,0,0,null))}function y(z,F){z.getRGB(Ic,wv(s)),i.buffers.color.setClear(Ic.r,Ic.g,Ic.b,F,u)}function S(){p!==void 0&&(p.geometry.dispose(),p.material.dispose(),p=void 0),m!==void 0&&(m.geometry.dispose(),m.material.dispose(),m=void 0)}return{getClearColor:function(){return d},setClearColor:function(z,F=1){d.set(z),h=F,y(d,h)},getClearAlpha:function(){return h},setClearAlpha:function(z){h=z,y(d,h)},render:T,addToRenderList:D,dispose:S}}function m1(s,e){const i=s.getParameter(s.MAX_VERTEX_ATTRIBS),r={},l=g(null);let u=l,d=!1;function h(V,Z,de,he,K){let N=!1;const I=x(V,he,de,Z);u!==I&&(u=I,p(u.object)),N=E(V,he,de,K),N&&T(V,he,de,K),K!==null&&e.update(K,s.ELEMENT_ARRAY_BUFFER),(N||d)&&(d=!1,C(V,Z,de,he),K!==null&&s.bindBuffer(s.ELEMENT_ARRAY_BUFFER,e.get(K).buffer))}function m(){return s.createVertexArray()}function p(V){return s.bindVertexArray(V)}function v(V){return s.deleteVertexArray(V)}function x(V,Z,de,he){const K=he.wireframe===!0;let N=r[Z.id];N===void 0&&(N={},r[Z.id]=N);const I=V.isInstancedMesh===!0?V.id:0;let $=N[I];$===void 0&&($={},N[I]=$);let se=$[de.id];se===void 0&&(se={},$[de.id]=se);let pe=se[K];return pe===void 0&&(pe=g(m()),se[K]=pe),pe}function g(V){const Z=[],de=[],he=[];for(let K=0;K<i;K++)Z[K]=0,de[K]=0,he[K]=0;return{geometry:null,program:null,wireframe:!1,newAttributes:Z,enabledAttributes:de,attributeDivisors:he,object:V,attributes:{},index:null}}function E(V,Z,de,he){const K=u.attributes,N=Z.attributes;let I=0;const $=de.getAttributes();for(const se in $)if($[se].location>=0){const xe=K[se];let Se=N[se];if(Se===void 0&&(se==="instanceMatrix"&&V.instanceMatrix&&(Se=V.instanceMatrix),se==="instanceColor"&&V.instanceColor&&(Se=V.instanceColor)),xe===void 0||xe.attribute!==Se||Se&&xe.data!==Se.data)return!0;I++}return u.attributesNum!==I||u.index!==he}function T(V,Z,de,he){const K={},N=Z.attributes;let I=0;const $=de.getAttributes();for(const se in $)if($[se].location>=0){let xe=N[se];xe===void 0&&(se==="instanceMatrix"&&V.instanceMatrix&&(xe=V.instanceMatrix),se==="instanceColor"&&V.instanceColor&&(xe=V.instanceColor));const Se={};Se.attribute=xe,xe&&xe.data&&(Se.data=xe.data),K[se]=Se,I++}u.attributes=K,u.attributesNum=I,u.index=he}function D(){const V=u.newAttributes;for(let Z=0,de=V.length;Z<de;Z++)V[Z]=0}function y(V){S(V,0)}function S(V,Z){const de=u.newAttributes,he=u.enabledAttributes,K=u.attributeDivisors;de[V]=1,he[V]===0&&(s.enableVertexAttribArray(V),he[V]=1),K[V]!==Z&&(s.vertexAttribDivisor(V,Z),K[V]=Z)}function z(){const V=u.newAttributes,Z=u.enabledAttributes;for(let de=0,he=Z.length;de<he;de++)Z[de]!==V[de]&&(s.disableVertexAttribArray(de),Z[de]=0)}function F(V,Z,de,he,K,N,I){I===!0?s.vertexAttribIPointer(V,Z,de,K,N):s.vertexAttribPointer(V,Z,de,he,K,N)}function C(V,Z,de,he){D();const K=he.attributes,N=de.getAttributes(),I=Z.defaultAttributeValues;for(const $ in N){const se=N[$];if(se.location>=0){let pe=K[$];if(pe===void 0&&($==="instanceMatrix"&&V.instanceMatrix&&(pe=V.instanceMatrix),$==="instanceColor"&&V.instanceColor&&(pe=V.instanceColor)),pe!==void 0){const xe=pe.normalized,Se=pe.itemSize,Te=e.get(pe);if(Te===void 0)continue;const tt=Te.buffer,nt=Te.type,ue=Te.bytesPerElement,Ae=nt===s.INT||nt===s.UNSIGNED_INT||pe.gpuType===Ih;if(pe.isInterleavedBufferAttribute){const Me=pe.data,$e=Me.stride,at=pe.offset;if(Me.isInstancedInterleavedBuffer){for(let Ze=0;Ze<se.locationSize;Ze++)S(se.location+Ze,Me.meshPerAttribute);V.isInstancedMesh!==!0&&he._maxInstanceCount===void 0&&(he._maxInstanceCount=Me.meshPerAttribute*Me.count)}else for(let Ze=0;Ze<se.locationSize;Ze++)y(se.location+Ze);s.bindBuffer(s.ARRAY_BUFFER,tt);for(let Ze=0;Ze<se.locationSize;Ze++)F(se.location+Ze,Se/se.locationSize,nt,xe,$e*ue,(at+Se/se.locationSize*Ze)*ue,Ae)}else{if(pe.isInstancedBufferAttribute){for(let Me=0;Me<se.locationSize;Me++)S(se.location+Me,pe.meshPerAttribute);V.isInstancedMesh!==!0&&he._maxInstanceCount===void 0&&(he._maxInstanceCount=pe.meshPerAttribute*pe.count)}else for(let Me=0;Me<se.locationSize;Me++)y(se.location+Me);s.bindBuffer(s.ARRAY_BUFFER,tt);for(let Me=0;Me<se.locationSize;Me++)F(se.location+Me,Se/se.locationSize,nt,xe,Se*ue,Se/se.locationSize*Me*ue,Ae)}}else if(I!==void 0){const xe=I[$];if(xe!==void 0)switch(xe.length){case 2:s.vertexAttrib2fv(se.location,xe);break;case 3:s.vertexAttrib3fv(se.location,xe);break;case 4:s.vertexAttrib4fv(se.location,xe);break;default:s.vertexAttrib1fv(se.location,xe)}}}}z()}function O(){L();for(const V in r){const Z=r[V];for(const de in Z){const he=Z[de];for(const K in he){const N=he[K];for(const I in N)v(N[I].object),delete N[I];delete he[K]}}delete r[V]}}function U(V){if(r[V.id]===void 0)return;const Z=r[V.id];for(const de in Z){const he=Z[de];for(const K in he){const N=he[K];for(const I in N)v(N[I].object),delete N[I];delete he[K]}}delete r[V.id]}function B(V){for(const Z in r){const de=r[Z];for(const he in de){const K=de[he];if(K[V.id]===void 0)continue;const N=K[V.id];for(const I in N)v(N[I].object),delete N[I];delete K[V.id]}}}function b(V){for(const Z in r){const de=r[Z],he=V.isInstancedMesh===!0?V.id:0,K=de[he];if(K!==void 0){for(const N in K){const I=K[N];for(const $ in I)v(I[$].object),delete I[$];delete K[N]}delete de[he],Object.keys(de).length===0&&delete r[Z]}}}function L(){W(),d=!0,u!==l&&(u=l,p(u.object))}function W(){l.geometry=null,l.program=null,l.wireframe=!1}return{setup:h,reset:L,resetDefaultState:W,dispose:O,releaseStatesOfGeometry:U,releaseStatesOfObject:b,releaseStatesOfProgram:B,initAttributes:D,enableAttribute:y,disableUnusedAttributes:z}}function g1(s,e,i){let r;function l(m){r=m}function u(m,p){s.drawArrays(r,m,p),i.update(p,r,1)}function d(m,p,v){v!==0&&(s.drawArraysInstanced(r,m,p,v),i.update(p,r,v))}function h(m,p,v){if(v===0)return;e.get("WEBGL_multi_draw").multiDrawArraysWEBGL(r,m,0,p,0,v);let g=0;for(let E=0;E<v;E++)g+=p[E];i.update(g,r,1)}this.setMode=l,this.render=u,this.renderInstances=d,this.renderMultiDraw=h}function _1(s,e,i,r){let l;function u(){if(l!==void 0)return l;if(e.has("EXT_texture_filter_anisotropic")===!0){const B=e.get("EXT_texture_filter_anisotropic");l=s.getParameter(B.MAX_TEXTURE_MAX_ANISOTROPY_EXT)}else l=0;return l}function d(B){return!(B!==Di&&r.convert(B)!==s.getParameter(s.IMPLEMENTATION_COLOR_READ_FORMAT))}function h(B){const b=B===ba&&(e.has("EXT_color_buffer_half_float")||e.has("EXT_color_buffer_float"));return!(B!==xi&&r.convert(B)!==s.getParameter(s.IMPLEMENTATION_COLOR_READ_TYPE)&&B!==Vi&&!b)}function m(B){if(B==="highp"){if(s.getShaderPrecisionFormat(s.VERTEX_SHADER,s.HIGH_FLOAT).precision>0&&s.getShaderPrecisionFormat(s.FRAGMENT_SHADER,s.HIGH_FLOAT).precision>0)return"highp";B="mediump"}return B==="mediump"&&s.getShaderPrecisionFormat(s.VERTEX_SHADER,s.MEDIUM_FLOAT).precision>0&&s.getShaderPrecisionFormat(s.FRAGMENT_SHADER,s.MEDIUM_FLOAT).precision>0?"mediump":"lowp"}let p=i.precision!==void 0?i.precision:"highp";const v=m(p);v!==p&&(et("WebGLRenderer:",p,"not supported, using",v,"instead."),p=v);const x=i.logarithmicDepthBuffer===!0,g=i.reversedDepthBuffer===!0&&e.has("EXT_clip_control");i.reversedDepthBuffer===!0&&g===!1&&et("WebGLRenderer: Unable to use reversed depth buffer due to missing EXT_clip_control extension. Fallback to default depth buffer.");const E=s.getParameter(s.MAX_TEXTURE_IMAGE_UNITS),T=s.getParameter(s.MAX_VERTEX_TEXTURE_IMAGE_UNITS),D=s.getParameter(s.MAX_TEXTURE_SIZE),y=s.getParameter(s.MAX_CUBE_MAP_TEXTURE_SIZE),S=s.getParameter(s.MAX_VERTEX_ATTRIBS),z=s.getParameter(s.MAX_VERTEX_UNIFORM_VECTORS),F=s.getParameter(s.MAX_VARYING_VECTORS),C=s.getParameter(s.MAX_FRAGMENT_UNIFORM_VECTORS),O=s.getParameter(s.MAX_SAMPLES),U=s.getParameter(s.SAMPLES);return{isWebGL2:!0,getMaxAnisotropy:u,getMaxPrecision:m,textureFormatReadable:d,textureTypeReadable:h,precision:p,logarithmicDepthBuffer:x,reversedDepthBuffer:g,maxTextures:E,maxVertexTextures:T,maxTextureSize:D,maxCubemapSize:y,maxAttributes:S,maxVertexUniforms:z,maxVaryings:F,maxFragmentUniforms:C,maxSamples:O,samples:U}}function v1(s){const e=this;let i=null,r=0,l=!1,u=!1;const d=new Os,h=new st,m={value:null,needsUpdate:!1};this.uniform=m,this.numPlanes=0,this.numIntersection=0,this.init=function(x,g){const E=x.length!==0||g||r!==0||l;return l=g,r=x.length,E},this.beginShadows=function(){u=!0,v(null)},this.endShadows=function(){u=!1},this.setGlobalState=function(x,g){i=v(x,g,0)},this.setState=function(x,g,E){const T=x.clippingPlanes,D=x.clipIntersection,y=x.clipShadows,S=s.get(x);if(!l||T===null||T.length===0||u&&!y)u?v(null):p();else{const z=u?0:r,F=z*4;let C=S.clippingState||null;m.value=C,C=v(T,g,F,E);for(let O=0;O!==F;++O)C[O]=i[O];S.clippingState=C,this.numIntersection=D?this.numPlanes:0,this.numPlanes+=z}};function p(){m.value!==i&&(m.value=i,m.needsUpdate=r>0),e.numPlanes=r,e.numIntersection=0}function v(x,g,E,T){const D=x!==null?x.length:0;let y=null;if(D!==0){if(y=m.value,T!==!0||y===null){const S=E+D*4,z=g.matrixWorldInverse;h.getNormalMatrix(z),(y===null||y.length<S)&&(y=new Float32Array(S));for(let F=0,C=E;F!==D;++F,C+=4)d.copy(x[F]).applyMatrix4(z,h),d.normal.toArray(y,C),y[C+3]=d.constant}m.value=y,m.needsUpdate=!0}return e.numPlanes=D,e.numIntersection=0,y}}const os=4,b_=[.125,.215,.35,.446,.526,.582],zs=20,x1=256,Wo=new Uv,T_=new Et;let Fd=null,Bd=0,Id=0,Hd=!1;const S1=new ae;class A_{constructor(e){this._renderer=e,this._pingPongRenderTarget=null,this._lodMax=0,this._cubeSize=0,this._sizeLods=[],this._sigmas=[],this._lodMeshes=[],this._backgroundBox=null,this._cubemapMaterial=null,this._equirectMaterial=null,this._blurMaterial=null,this._ggxMaterial=null}fromScene(e,i=0,r=.1,l=100,u={}){const{size:d=256,position:h=S1}=u;Fd=this._renderer.getRenderTarget(),Bd=this._renderer.getActiveCubeFace(),Id=this._renderer.getActiveMipmapLevel(),Hd=this._renderer.xr.enabled,this._renderer.xr.enabled=!1,this._setSize(d);const m=this._allocateTargets();return m.depthBuffer=!0,this._sceneToCubeUV(e,r,l,m,h),i>0&&this._blur(m,0,0,i),this._applyPMREM(m),this._cleanup(m),m}fromEquirectangular(e,i=null){return this._fromTexture(e,i)}fromCubemap(e,i=null){return this._fromTexture(e,i)}compileCubemapShader(){this._cubemapMaterial===null&&(this._cubemapMaterial=w_(),this._compileMaterial(this._cubemapMaterial))}compileEquirectangularShader(){this._equirectMaterial===null&&(this._equirectMaterial=C_(),this._compileMaterial(this._equirectMaterial))}dispose(){this._dispose(),this._cubemapMaterial!==null&&this._cubemapMaterial.dispose(),this._equirectMaterial!==null&&this._equirectMaterial.dispose(),this._backgroundBox!==null&&(this._backgroundBox.geometry.dispose(),this._backgroundBox.material.dispose())}_setSize(e){this._lodMax=Math.floor(Math.log2(e)),this._cubeSize=Math.pow(2,this._lodMax)}_dispose(){this._blurMaterial!==null&&this._blurMaterial.dispose(),this._ggxMaterial!==null&&this._ggxMaterial.dispose(),this._pingPongRenderTarget!==null&&this._pingPongRenderTarget.dispose();for(let e=0;e<this._lodMeshes.length;e++)this._lodMeshes[e].geometry.dispose()}_cleanup(e){this._renderer.setRenderTarget(Fd,Bd,Id),this._renderer.xr.enabled=Hd,e.scissorTest=!1,Or(e,0,0,e.width,e.height)}_fromTexture(e,i){e.mapping===Is||e.mapping===Gr?this._setSize(e.image.length===0?16:e.image[0].width||e.image[0].image.width):this._setSize(e.image.width/4),Fd=this._renderer.getRenderTarget(),Bd=this._renderer.getActiveCubeFace(),Id=this._renderer.getActiveMipmapLevel(),Hd=this._renderer.xr.enabled,this._renderer.xr.enabled=!1;const r=i||this._allocateTargets();return this._textureToCubeUV(e,r),this._applyPMREM(r),this._cleanup(r),r}_allocateTargets(){const e=3*Math.max(this._cubeSize,112),i=4*this._cubeSize,r={magFilter:On,minFilter:On,generateMipmaps:!1,type:ba,format:Di,colorSpace:Kc,depthBuffer:!1},l=R_(e,i,r);if(this._pingPongRenderTarget===null||this._pingPongRenderTarget.width!==e||this._pingPongRenderTarget.height!==i){this._pingPongRenderTarget!==null&&this._dispose(),this._pingPongRenderTarget=R_(e,i,r);const{_lodMax:u}=this;({lodMeshes:this._lodMeshes,sizeLods:this._sizeLods,sigmas:this._sigmas}=M1(u)),this._blurMaterial=E1(u,e,i),this._ggxMaterial=y1(u,e,i)}return l}_compileMaterial(e){const i=new Ui(new Li,e);this._renderer.compile(i,Wo)}_sceneToCubeUV(e,i,r,l,u){const m=new vi(90,1,i,r),p=[1,-1,1,1,1,1],v=[1,1,1,-1,-1,-1],x=this._renderer,g=x.autoClear,E=x.toneMapping;x.getClearColor(T_),x.toneMapping=Xi,x.autoClear=!1,x.state.buffers.depth.getReversed()&&(x.setRenderTarget(l),x.clearDepth(),x.setRenderTarget(null)),this._backgroundBox===null&&(this._backgroundBox=new Ui(new nl,new Qh({name:"PMREM.Background",side:Yn,depthWrite:!1,depthTest:!1})));const D=this._backgroundBox,y=D.material;let S=!1;const z=e.background;z?z.isColor&&(y.color.copy(z),e.background=null,S=!0):(y.color.copy(T_),S=!0);for(let F=0;F<6;F++){const C=F%3;C===0?(m.up.set(0,p[F],0),m.position.set(u.x,u.y,u.z),m.lookAt(u.x+v[F],u.y,u.z)):C===1?(m.up.set(0,0,p[F]),m.position.set(u.x,u.y,u.z),m.lookAt(u.x,u.y+v[F],u.z)):(m.up.set(0,p[F],0),m.position.set(u.x,u.y,u.z),m.lookAt(u.x,u.y,u.z+v[F]));const O=this._cubeSize;Or(l,C*O,F>2?O:0,O,O),x.setRenderTarget(l),S&&x.render(D,m),x.render(e,m)}x.toneMapping=E,x.autoClear=g,e.background=z}_textureToCubeUV(e,i){const r=this._renderer,l=e.mapping===Is||e.mapping===Gr;l?(this._cubemapMaterial===null&&(this._cubemapMaterial=w_()),this._cubemapMaterial.uniforms.flipEnvMap.value=e.isRenderTargetTexture===!1?-1:1):this._equirectMaterial===null&&(this._equirectMaterial=C_());const u=l?this._cubemapMaterial:this._equirectMaterial,d=this._lodMeshes[0];d.material=u;const h=u.uniforms;h.envMap.value=e;const m=this._cubeSize;Or(i,0,0,3*m,2*m),r.setRenderTarget(i),r.render(d,Wo)}_applyPMREM(e){const i=this._renderer,r=i.autoClear;i.autoClear=!1;const l=this._lodMeshes.length;for(let u=1;u<l;u++)this._applyGGXFilter(e,u-1,u);i.autoClear=r}_applyGGXFilter(e,i,r){const l=this._renderer,u=this._pingPongRenderTarget,d=this._ggxMaterial,h=this._lodMeshes[r];h.material=d;const m=d.uniforms,p=r/(this._lodMeshes.length-1),v=i/(this._lodMeshes.length-1),x=Math.sqrt(p*p-v*v),g=0+p*1.25,E=x*g,{_lodMax:T}=this,D=this._sizeLods[r],y=3*D*(r>T-os?r-T+os:0),S=4*(this._cubeSize-D);m.envMap.value=e.texture,m.roughness.value=E,m.mipInt.value=T-i,Or(u,y,S,3*D,2*D),l.setRenderTarget(u),l.render(h,Wo),m.envMap.value=u.texture,m.roughness.value=0,m.mipInt.value=T-r,Or(e,y,S,3*D,2*D),l.setRenderTarget(e),l.render(h,Wo)}_blur(e,i,r,l,u){const d=this._pingPongRenderTarget;this._halfBlur(e,d,i,r,l,"latitudinal",u),this._halfBlur(d,e,r,r,l,"longitudinal",u)}_halfBlur(e,i,r,l,u,d,h){const m=this._renderer,p=this._blurMaterial;d!=="latitudinal"&&d!=="longitudinal"&&bt("blur direction must be either latitudinal or longitudinal!");const v=3,x=this._lodMeshes[l];x.material=p;const g=p.uniforms,E=this._sizeLods[r]-1,T=isFinite(u)?Math.PI/(2*E):2*Math.PI/(2*zs-1),D=u/T,y=isFinite(u)?1+Math.floor(v*D):zs;y>zs&&et(`sigmaRadians, ${u}, is too large and will clip, as it requested ${y} samples when the maximum is set to ${zs}`);const S=[];let z=0;for(let B=0;B<zs;++B){const b=B/D,L=Math.exp(-b*b/2);S.push(L),B===0?z+=L:B<y&&(z+=2*L)}for(let B=0;B<S.length;B++)S[B]=S[B]/z;g.envMap.value=e.texture,g.samples.value=y,g.weights.value=S,g.latitudinal.value=d==="latitudinal",h&&(g.poleAxis.value=h);const{_lodMax:F}=this;g.dTheta.value=T,g.mipInt.value=F-r;const C=this._sizeLods[l],O=3*C*(l>F-os?l-F+os:0),U=4*(this._cubeSize-C);Or(i,O,U,3*C,2*C),m.setRenderTarget(i),m.render(x,Wo)}}function M1(s){const e=[],i=[],r=[];let l=s;const u=s-os+1+b_.length;for(let d=0;d<u;d++){const h=Math.pow(2,l);e.push(h);let m=1/h;d>s-os?m=b_[d-s+os-1]:d===0&&(m=0),i.push(m);const p=1/(h-2),v=-p,x=1+p,g=[v,v,x,v,x,x,v,v,x,x,v,x],E=6,T=6,D=3,y=2,S=1,z=new Float32Array(D*T*E),F=new Float32Array(y*T*E),C=new Float32Array(S*T*E);for(let U=0;U<E;U++){const B=U%3*2/3-1,b=U>2?0:-1,L=[B,b,0,B+2/3,b,0,B+2/3,b+1,0,B,b,0,B+2/3,b+1,0,B,b+1,0];z.set(L,D*T*U),F.set(g,y*T*U);const W=[U,U,U,U,U,U];C.set(W,S*T*U)}const O=new Li;O.setAttribute("position",new Si(z,D)),O.setAttribute("uv",new Si(F,y)),O.setAttribute("faceIndex",new Si(C,S)),r.push(new Ui(O,null)),l>os&&l--}return{lodMeshes:r,sizeLods:e,sigmas:i}}function R_(s,e,i){const r=new Wi(s,e,i);return r.texture.mapping=eu,r.texture.name="PMREM.cubeUv",r.scissorTest=!0,r}function Or(s,e,i,r,l){s.viewport.set(e,i,r,l),s.scissor.set(e,i,r,l)}function y1(s,e,i){return new Yi({name:"PMREMGGXConvolution",defines:{GGX_SAMPLES:x1,CUBEUV_TEXEL_WIDTH:1/e,CUBEUV_TEXEL_HEIGHT:1/i,CUBEUV_MAX_MIP:`${s}.0`},uniforms:{envMap:{value:null},roughness:{value:0},mipInt:{value:0}},vertexShader:nu(),fragmentShader:`

			precision highp float;
			precision highp int;

			varying vec3 vOutputDirection;

			uniform sampler2D envMap;
			uniform float roughness;
			uniform float mipInt;

			#define ENVMAP_TYPE_CUBE_UV
			#include <cube_uv_reflection_fragment>

			#define PI 3.14159265359

			// Van der Corput radical inverse
			float radicalInverse_VdC(uint bits) {
				bits = (bits << 16u) | (bits >> 16u);
				bits = ((bits & 0x55555555u) << 1u) | ((bits & 0xAAAAAAAAu) >> 1u);
				bits = ((bits & 0x33333333u) << 2u) | ((bits & 0xCCCCCCCCu) >> 2u);
				bits = ((bits & 0x0F0F0F0Fu) << 4u) | ((bits & 0xF0F0F0F0u) >> 4u);
				bits = ((bits & 0x00FF00FFu) << 8u) | ((bits & 0xFF00FF00u) >> 8u);
				return float(bits) * 2.3283064365386963e-10; // / 0x100000000
			}

			// Hammersley sequence
			vec2 hammersley(uint i, uint N) {
				return vec2(float(i) / float(N), radicalInverse_VdC(i));
			}

			// GGX VNDF importance sampling (Eric Heitz 2018)
			// "Sampling the GGX Distribution of Visible Normals"
			// https://jcgt.org/published/0007/04/01/
			vec3 importanceSampleGGX_VNDF(vec2 Xi, vec3 V, float roughness) {
				float alpha = roughness * roughness;

				// Section 4.1: Orthonormal basis
				vec3 T1 = vec3(1.0, 0.0, 0.0);
				vec3 T2 = cross(V, T1);

				// Section 4.2: Parameterization of projected area
				float r = sqrt(Xi.x);
				float phi = 2.0 * PI * Xi.y;
				float t1 = r * cos(phi);
				float t2 = r * sin(phi);
				float s = 0.5 * (1.0 + V.z);
				t2 = (1.0 - s) * sqrt(1.0 - t1 * t1) + s * t2;

				// Section 4.3: Reprojection onto hemisphere
				vec3 Nh = t1 * T1 + t2 * T2 + sqrt(max(0.0, 1.0 - t1 * t1 - t2 * t2)) * V;

				// Section 3.4: Transform back to ellipsoid configuration
				return normalize(vec3(alpha * Nh.x, alpha * Nh.y, max(0.0, Nh.z)));
			}

			void main() {
				vec3 N = normalize(vOutputDirection);
				vec3 V = N; // Assume view direction equals normal for pre-filtering

				vec3 prefilteredColor = vec3(0.0);
				float totalWeight = 0.0;

				// For very low roughness, just sample the environment directly
				if (roughness < 0.001) {
					gl_FragColor = vec4(bilinearCubeUV(envMap, N, mipInt), 1.0);
					return;
				}

				// Tangent space basis for VNDF sampling
				vec3 up = abs(N.z) < 0.999 ? vec3(0.0, 0.0, 1.0) : vec3(1.0, 0.0, 0.0);
				vec3 tangent = normalize(cross(up, N));
				vec3 bitangent = cross(N, tangent);

				for(uint i = 0u; i < uint(GGX_SAMPLES); i++) {
					vec2 Xi = hammersley(i, uint(GGX_SAMPLES));

					// For PMREM, V = N, so in tangent space V is always (0, 0, 1)
					vec3 H_tangent = importanceSampleGGX_VNDF(Xi, vec3(0.0, 0.0, 1.0), roughness);

					// Transform H back to world space
					vec3 H = normalize(tangent * H_tangent.x + bitangent * H_tangent.y + N * H_tangent.z);
					vec3 L = normalize(2.0 * dot(V, H) * H - V);

					float NdotL = max(dot(N, L), 0.0);

					if(NdotL > 0.0) {
						// Sample environment at fixed mip level
						// VNDF importance sampling handles the distribution filtering
						vec3 sampleColor = bilinearCubeUV(envMap, L, mipInt);

						// Weight by NdotL for the split-sum approximation
						// VNDF PDF naturally accounts for the visible microfacet distribution
						prefilteredColor += sampleColor * NdotL;
						totalWeight += NdotL;
					}
				}

				if (totalWeight > 0.0) {
					prefilteredColor = prefilteredColor / totalWeight;
				}

				gl_FragColor = vec4(prefilteredColor, 1.0);
			}
		`,blending:Ma,depthTest:!1,depthWrite:!1})}function E1(s,e,i){const r=new Float32Array(zs),l=new ae(0,1,0);return new Yi({name:"SphericalGaussianBlur",defines:{n:zs,CUBEUV_TEXEL_WIDTH:1/e,CUBEUV_TEXEL_HEIGHT:1/i,CUBEUV_MAX_MIP:`${s}.0`},uniforms:{envMap:{value:null},samples:{value:1},weights:{value:r},latitudinal:{value:!1},dTheta:{value:0},mipInt:{value:0},poleAxis:{value:l}},vertexShader:nu(),fragmentShader:`

			precision mediump float;
			precision mediump int;

			varying vec3 vOutputDirection;

			uniform sampler2D envMap;
			uniform int samples;
			uniform float weights[ n ];
			uniform bool latitudinal;
			uniform float dTheta;
			uniform float mipInt;
			uniform vec3 poleAxis;

			#define ENVMAP_TYPE_CUBE_UV
			#include <cube_uv_reflection_fragment>

			vec3 getSample( float theta, vec3 axis ) {

				float cosTheta = cos( theta );
				// Rodrigues' axis-angle rotation
				vec3 sampleDirection = vOutputDirection * cosTheta
					+ cross( axis, vOutputDirection ) * sin( theta )
					+ axis * dot( axis, vOutputDirection ) * ( 1.0 - cosTheta );

				return bilinearCubeUV( envMap, sampleDirection, mipInt );

			}

			void main() {

				vec3 axis = latitudinal ? poleAxis : cross( poleAxis, vOutputDirection );

				if ( all( equal( axis, vec3( 0.0 ) ) ) ) {

					axis = vec3( vOutputDirection.z, 0.0, - vOutputDirection.x );

				}

				axis = normalize( axis );

				gl_FragColor = vec4( 0.0, 0.0, 0.0, 1.0 );
				gl_FragColor.rgb += weights[ 0 ] * getSample( 0.0, axis );

				for ( int i = 1; i < n; i++ ) {

					if ( i >= samples ) {

						break;

					}

					float theta = dTheta * float( i );
					gl_FragColor.rgb += weights[ i ] * getSample( -1.0 * theta, axis );
					gl_FragColor.rgb += weights[ i ] * getSample( theta, axis );

				}

			}
		`,blending:Ma,depthTest:!1,depthWrite:!1})}function C_(){return new Yi({name:"EquirectangularToCubeUV",uniforms:{envMap:{value:null}},vertexShader:nu(),fragmentShader:`

			precision mediump float;
			precision mediump int;

			varying vec3 vOutputDirection;

			uniform sampler2D envMap;

			#include <common>

			void main() {

				vec3 outputDirection = normalize( vOutputDirection );
				vec2 uv = equirectUv( outputDirection );

				gl_FragColor = vec4( texture2D ( envMap, uv ).rgb, 1.0 );

			}
		`,blending:Ma,depthTest:!1,depthWrite:!1})}function w_(){return new Yi({name:"CubemapToCubeUV",uniforms:{envMap:{value:null},flipEnvMap:{value:-1}},vertexShader:nu(),fragmentShader:`

			precision mediump float;
			precision mediump int;

			uniform float flipEnvMap;

			varying vec3 vOutputDirection;

			uniform samplerCube envMap;

			void main() {

				gl_FragColor = textureCube( envMap, vec3( flipEnvMap * vOutputDirection.x, vOutputDirection.yz ) );

			}
		`,blending:Ma,depthTest:!1,depthWrite:!1})}function nu(){return`

		precision mediump float;
		precision mediump int;

		attribute float faceIndex;

		varying vec3 vOutputDirection;

		// RH coordinate system; PMREM face-indexing convention
		vec3 getDirection( vec2 uv, float face ) {

			uv = 2.0 * uv - 1.0;

			vec3 direction = vec3( uv, 1.0 );

			if ( face == 0.0 ) {

				direction = direction.zyx; // ( 1, v, u ) pos x

			} else if ( face == 1.0 ) {

				direction = direction.xzy;
				direction.xz *= -1.0; // ( -u, 1, -v ) pos y

			} else if ( face == 2.0 ) {

				direction.x *= -1.0; // ( -u, v, 1 ) pos z

			} else if ( face == 3.0 ) {

				direction = direction.zyx;
				direction.xz *= -1.0; // ( -1, v, -u ) neg x

			} else if ( face == 4.0 ) {

				direction = direction.xzy;
				direction.xy *= -1.0; // ( -u, -1, v ) neg y

			} else if ( face == 5.0 ) {

				direction.z *= -1.0; // ( u, v, -1 ) neg z

			}

			return direction;

		}

		void main() {

			vOutputDirection = getDirection( uv, faceIndex );
			gl_Position = vec4( position, 1.0 );

		}
	`}class Ov extends Wi{constructor(e=1,i={}){super(e,e,i),this.isWebGLCubeRenderTarget=!0;const r={width:e,height:e,depth:1},l=[r,r,r,r,r,r];this.texture=new Rv(l),this._setTextureOptions(i),this.texture.isRenderTargetTexture=!0}fromEquirectangularTexture(e,i){this.texture.type=i.type,this.texture.colorSpace=i.colorSpace,this.texture.generateMipmaps=i.generateMipmaps,this.texture.minFilter=i.minFilter,this.texture.magFilter=i.magFilter;const r={uniforms:{tEquirect:{value:null}},vertexShader:`

				varying vec3 vWorldDirection;

				vec3 transformDirection( in vec3 dir, in mat4 matrix ) {

					return normalize( ( matrix * vec4( dir, 0.0 ) ).xyz );

				}

				void main() {

					vWorldDirection = transformDirection( position, modelMatrix );

					#include <begin_vertex>
					#include <project_vertex>

				}
			`,fragmentShader:`

				uniform sampler2D tEquirect;

				varying vec3 vWorldDirection;

				#include <common>

				void main() {

					vec3 direction = normalize( vWorldDirection );

					vec2 sampleUV = equirectUv( direction );

					gl_FragColor = texture2D( tEquirect, sampleUV );

				}
			`},l=new nl(5,5,5),u=new Yi({name:"CubemapFromEquirect",uniforms:kr(r.uniforms),vertexShader:r.vertexShader,fragmentShader:r.fragmentShader,side:Yn,blending:Ma});u.uniforms.tEquirect.value=i;const d=new Ui(l,u),h=i.minFilter;return i.minFilter===Fs&&(i.minFilter=On),new CE(1,10,this).update(e,d),i.minFilter=h,d.geometry.dispose(),d.material.dispose(),this}clear(e,i=!0,r=!0,l=!0){const u=e.getRenderTarget();for(let d=0;d<6;d++)e.setRenderTarget(this,d),e.clear(i,r,l);e.setRenderTarget(u)}}function b1(s){let e=new WeakMap,i=new WeakMap,r=null;function l(g,E=!1){return g==null?null:E?d(g):u(g)}function u(g){if(g&&g.isTexture){const E=g.mapping;if(E===dd||E===hd)if(e.has(g)){const T=e.get(g).texture;return h(T,g.mapping)}else{const T=g.image;if(T&&T.height>0){const D=new Ov(T.height);return D.fromEquirectangularTexture(s,g),e.set(g,D),g.addEventListener("dispose",p),h(D.texture,g.mapping)}else return null}}return g}function d(g){if(g&&g.isTexture){const E=g.mapping,T=E===dd||E===hd,D=E===Is||E===Gr;if(T||D){let y=i.get(g);const S=y!==void 0?y.texture.pmremVersion:0;if(g.isRenderTargetTexture&&g.pmremVersion!==S)return r===null&&(r=new A_(s)),y=T?r.fromEquirectangular(g,y):r.fromCubemap(g,y),y.texture.pmremVersion=g.pmremVersion,i.set(g,y),y.texture;if(y!==void 0)return y.texture;{const z=g.image;return T&&z&&z.height>0||D&&z&&m(z)?(r===null&&(r=new A_(s)),y=T?r.fromEquirectangular(g):r.fromCubemap(g),y.texture.pmremVersion=g.pmremVersion,i.set(g,y),g.addEventListener("dispose",v),y.texture):null}}}return g}function h(g,E){return E===dd?g.mapping=Is:E===hd&&(g.mapping=Gr),g}function m(g){let E=0;const T=6;for(let D=0;D<T;D++)g[D]!==void 0&&E++;return E===T}function p(g){const E=g.target;E.removeEventListener("dispose",p);const T=e.get(E);T!==void 0&&(e.delete(E),T.dispose())}function v(g){const E=g.target;E.removeEventListener("dispose",v);const T=i.get(E);T!==void 0&&(i.delete(E),T.dispose())}function x(){e=new WeakMap,i=new WeakMap,r!==null&&(r.dispose(),r=null)}return{get:l,dispose:x}}function T1(s){const e={};function i(r){if(e[r]!==void 0)return e[r];const l=s.getExtension(r);return e[r]=l,l}return{has:function(r){return i(r)!==null},init:function(){i("EXT_color_buffer_float"),i("WEBGL_clip_cull_distance"),i("OES_texture_float_linear"),i("EXT_color_buffer_half_float"),i("WEBGL_multisampled_render_to_texture"),i("WEBGL_render_shared_exponent")},get:function(r){const l=i(r);return l===null&&Br("WebGLRenderer: "+r+" extension not supported."),l}}}function A1(s,e,i,r){const l={},u=new WeakMap;function d(x){const g=x.target;g.index!==null&&e.remove(g.index);for(const T in g.attributes)e.remove(g.attributes[T]);g.removeEventListener("dispose",d),delete l[g.id];const E=u.get(g);E&&(e.remove(E),u.delete(g)),r.releaseStatesOfGeometry(g),g.isInstancedBufferGeometry===!0&&delete g._maxInstanceCount,i.memory.geometries--}function h(x,g){return l[g.id]===!0||(g.addEventListener("dispose",d),l[g.id]=!0,i.memory.geometries++),g}function m(x){const g=x.attributes;for(const E in g)e.update(g[E],s.ARRAY_BUFFER)}function p(x){const g=[],E=x.index,T=x.attributes.position;let D=0;if(T===void 0)return;if(E!==null){const z=E.array;D=E.version;for(let F=0,C=z.length;F<C;F+=3){const O=z[F+0],U=z[F+1],B=z[F+2];g.push(O,U,U,B,B,O)}}else{const z=T.array;D=T.version;for(let F=0,C=z.length/3-1;F<C;F+=3){const O=F+0,U=F+1,B=F+2;g.push(O,U,U,B,B,O)}}const y=new(T.count>=65535?Ev:yv)(g,1);y.version=D;const S=u.get(x);S&&e.remove(S),u.set(x,y)}function v(x){const g=u.get(x);if(g){const E=x.index;E!==null&&g.version<E.version&&p(x)}else p(x);return u.get(x)}return{get:h,update:m,getWireframeAttribute:v}}function R1(s,e,i){let r;function l(x){r=x}let u,d;function h(x){u=x.type,d=x.bytesPerElement}function m(x,g){s.drawElements(r,g,u,x*d),i.update(g,r,1)}function p(x,g,E){E!==0&&(s.drawElementsInstanced(r,g,u,x*d,E),i.update(g,r,E))}function v(x,g,E){if(E===0)return;e.get("WEBGL_multi_draw").multiDrawElementsWEBGL(r,g,0,u,x,0,E);let D=0;for(let y=0;y<E;y++)D+=g[y];i.update(D,r,1)}this.setMode=l,this.setIndex=h,this.render=m,this.renderInstances=p,this.renderMultiDraw=v}function C1(s){const e={geometries:0,textures:0},i={frame:0,calls:0,triangles:0,points:0,lines:0};function r(u,d,h){switch(i.calls++,d){case s.TRIANGLES:i.triangles+=h*(u/3);break;case s.LINES:i.lines+=h*(u/2);break;case s.LINE_STRIP:i.lines+=h*(u-1);break;case s.LINE_LOOP:i.lines+=h*u;break;case s.POINTS:i.points+=h*u;break;default:bt("WebGLInfo: Unknown draw mode:",d);break}}function l(){i.calls=0,i.triangles=0,i.points=0,i.lines=0}return{memory:e,render:i,programs:null,autoReset:!0,reset:l,update:r}}function w1(s,e,i){const r=new WeakMap,l=new on;function u(d,h,m){const p=d.morphTargetInfluences,v=h.morphAttributes.position||h.morphAttributes.normal||h.morphAttributes.color,x=v!==void 0?v.length:0;let g=r.get(h);if(g===void 0||g.count!==x){let L=function(){B.dispose(),r.delete(h),h.removeEventListener("dispose",L)};g!==void 0&&g.texture.dispose();const E=h.morphAttributes.position!==void 0,T=h.morphAttributes.normal!==void 0,D=h.morphAttributes.color!==void 0,y=h.morphAttributes.position||[],S=h.morphAttributes.normal||[],z=h.morphAttributes.color||[];let F=0;E===!0&&(F=1),T===!0&&(F=2),D===!0&&(F=3);let C=h.attributes.position.count*F,O=1;C>e.maxTextureSize&&(O=Math.ceil(C/e.maxTextureSize),C=e.maxTextureSize);const U=new Float32Array(C*O*4*x),B=new xv(U,C,O,x);B.type=Vi,B.needsUpdate=!0;const b=F*4;for(let W=0;W<x;W++){const V=y[W],Z=S[W],de=z[W],he=C*O*4*W;for(let K=0;K<V.count;K++){const N=K*b;E===!0&&(l.fromBufferAttribute(V,K),U[he+N+0]=l.x,U[he+N+1]=l.y,U[he+N+2]=l.z,U[he+N+3]=0),T===!0&&(l.fromBufferAttribute(Z,K),U[he+N+4]=l.x,U[he+N+5]=l.y,U[he+N+6]=l.z,U[he+N+7]=0),D===!0&&(l.fromBufferAttribute(de,K),U[he+N+8]=l.x,U[he+N+9]=l.y,U[he+N+10]=l.z,U[he+N+11]=de.itemSize===4?l.w:1)}}g={count:x,texture:B,size:new Ut(C,O)},r.set(h,g),h.addEventListener("dispose",L)}if(d.isInstancedMesh===!0&&d.morphTexture!==null)m.getUniforms().setValue(s,"morphTexture",d.morphTexture,i);else{let E=0;for(let D=0;D<p.length;D++)E+=p[D];const T=h.morphTargetsRelative?1:1-E;m.getUniforms().setValue(s,"morphTargetBaseInfluence",T),m.getUniforms().setValue(s,"morphTargetInfluences",p)}m.getUniforms().setValue(s,"morphTargetsTexture",g.texture,i),m.getUniforms().setValue(s,"morphTargetsTextureSize",g.size)}return{update:u}}function D1(s,e,i,r,l){let u=new WeakMap;function d(p){const v=l.render.frame,x=p.geometry,g=e.get(p,x);if(u.get(g)!==v&&(e.update(g),u.set(g,v)),p.isInstancedMesh&&(p.hasEventListener("dispose",m)===!1&&p.addEventListener("dispose",m),u.get(p)!==v&&(i.update(p.instanceMatrix,s.ARRAY_BUFFER),p.instanceColor!==null&&i.update(p.instanceColor,s.ARRAY_BUFFER),u.set(p,v))),p.isSkinnedMesh){const E=p.skeleton;u.get(E)!==v&&(E.update(),u.set(E,v))}return g}function h(){u=new WeakMap}function m(p){const v=p.target;v.removeEventListener("dispose",m),r.releaseStatesOfObject(v),i.remove(v.instanceMatrix),v.instanceColor!==null&&i.remove(v.instanceColor)}return{update:d,dispose:h}}const U1={[iv]:"LINEAR_TONE_MAPPING",[av]:"REINHARD_TONE_MAPPING",[sv]:"CINEON_TONE_MAPPING",[rv]:"ACES_FILMIC_TONE_MAPPING",[lv]:"AGX_TONE_MAPPING",[cv]:"NEUTRAL_TONE_MAPPING",[ov]:"CUSTOM_TONE_MAPPING"};function L1(s,e,i,r,l,u){const d=new Wi(e,i,{type:s,depthBuffer:l,stencilBuffer:u,samples:r?4:0,depthTexture:l?new Vr(e,i):void 0}),h=new Wi(e,i,{type:ba,depthBuffer:!1,stencilBuffer:!1}),m=new Li;m.setAttribute("position",new Ea([-1,3,0,-1,-1,0,3,-1,0],3)),m.setAttribute("uv",new Ea([0,2,0,0,2,0],2));const p=new TE({uniforms:{tDiffuse:{value:null}},vertexShader:`
			precision highp float;

			uniform mat4 modelViewMatrix;
			uniform mat4 projectionMatrix;

			attribute vec3 position;
			attribute vec2 uv;

			varying vec2 vUv;

			void main() {
				vUv = uv;
				gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );
			}`,fragmentShader:`
			precision highp float;

			uniform sampler2D tDiffuse;

			varying vec2 vUv;

			#include <tonemapping_pars_fragment>
			#include <colorspace_pars_fragment>

			void main() {
				gl_FragColor = texture2D( tDiffuse, vUv );

				#ifdef LINEAR_TONE_MAPPING
					gl_FragColor.rgb = LinearToneMapping( gl_FragColor.rgb );
				#elif defined( REINHARD_TONE_MAPPING )
					gl_FragColor.rgb = ReinhardToneMapping( gl_FragColor.rgb );
				#elif defined( CINEON_TONE_MAPPING )
					gl_FragColor.rgb = CineonToneMapping( gl_FragColor.rgb );
				#elif defined( ACES_FILMIC_TONE_MAPPING )
					gl_FragColor.rgb = ACESFilmicToneMapping( gl_FragColor.rgb );
				#elif defined( AGX_TONE_MAPPING )
					gl_FragColor.rgb = AgXToneMapping( gl_FragColor.rgb );
				#elif defined( NEUTRAL_TONE_MAPPING )
					gl_FragColor.rgb = NeutralToneMapping( gl_FragColor.rgb );
				#elif defined( CUSTOM_TONE_MAPPING )
					gl_FragColor.rgb = CustomToneMapping( gl_FragColor.rgb );
				#endif

				#ifdef SRGB_TRANSFER
					gl_FragColor = sRGBTransferOETF( gl_FragColor );
				#endif
			}`,depthTest:!1,depthWrite:!1}),v=new Ui(m,p),x=new Uv(-1,1,1,-1,0,1);let g=null,E=null,T=!1,D,y=null,S=[],z=!1;this.setSize=function(F,C){d.setSize(F,C),h.setSize(F,C);for(let O=0;O<S.length;O++){const U=S[O];U.setSize&&U.setSize(F,C)}},this.setEffects=function(F){S=F,z=S.length>0&&S[0].isRenderPass===!0;const C=d.width,O=d.height;for(let U=0;U<S.length;U++){const B=S[U];B.setSize&&B.setSize(C,O)}},this.begin=function(F,C){if(T||F.toneMapping===Xi&&S.length===0)return!1;if(y=C,C!==null){const O=C.width,U=C.height;(d.width!==O||d.height!==U)&&this.setSize(O,U)}return z===!1&&F.setRenderTarget(d),D=F.toneMapping,F.toneMapping=Xi,!0},this.hasRenderPass=function(){return z},this.end=function(F,C){F.toneMapping=D,T=!0;let O=d,U=h;for(let B=0;B<S.length;B++){const b=S[B];if(b.enabled!==!1&&(b.render(F,U,O,C),b.needsSwap!==!1)){const L=O;O=U,U=L}}if(g!==F.outputColorSpace||E!==F.toneMapping){g=F.outputColorSpace,E=F.toneMapping,p.defines={},yt.getTransfer(g)===It&&(p.defines.SRGB_TRANSFER="");const B=U1[E];B&&(p.defines[B]=""),p.needsUpdate=!0}p.uniforms.tDiffuse.value=O.texture,F.setRenderTarget(y),F.render(v,x),y=null,T=!1},this.isCompositing=function(){return T},this.dispose=function(){d.depthTexture&&d.depthTexture.dispose(),d.dispose(),h.dispose(),m.dispose(),p.dispose()}}const Pv=new Bn,zh=new Vr(1,1),zv=new xv,Fv=new tE,Bv=new Rv,D_=[],U_=[],L_=new Float32Array(16),N_=new Float32Array(9),O_=new Float32Array(4);function qr(s,e,i){const r=s[0];if(r<=0||r>0)return s;const l=e*i;let u=D_[l];if(u===void 0&&(u=new Float32Array(l),D_[l]=u),e!==0){r.toArray(u,0);for(let d=1,h=0;d!==e;++d)h+=i,s[d].toArray(u,h)}return u}function Mn(s,e){if(s.length!==e.length)return!1;for(let i=0,r=s.length;i<r;i++)if(s[i]!==e[i])return!1;return!0}function yn(s,e){for(let i=0,r=e.length;i<r;i++)s[i]=e[i]}function iu(s,e){let i=U_[e];i===void 0&&(i=new Int32Array(e),U_[e]=i);for(let r=0;r!==e;++r)i[r]=s.allocateTextureUnit();return i}function N1(s,e){const i=this.cache;i[0]!==e&&(s.uniform1f(this.addr,e),i[0]=e)}function O1(s,e){const i=this.cache;if(e.x!==void 0)(i[0]!==e.x||i[1]!==e.y)&&(s.uniform2f(this.addr,e.x,e.y),i[0]=e.x,i[1]=e.y);else{if(Mn(i,e))return;s.uniform2fv(this.addr,e),yn(i,e)}}function P1(s,e){const i=this.cache;if(e.x!==void 0)(i[0]!==e.x||i[1]!==e.y||i[2]!==e.z)&&(s.uniform3f(this.addr,e.x,e.y,e.z),i[0]=e.x,i[1]=e.y,i[2]=e.z);else if(e.r!==void 0)(i[0]!==e.r||i[1]!==e.g||i[2]!==e.b)&&(s.uniform3f(this.addr,e.r,e.g,e.b),i[0]=e.r,i[1]=e.g,i[2]=e.b);else{if(Mn(i,e))return;s.uniform3fv(this.addr,e),yn(i,e)}}function z1(s,e){const i=this.cache;if(e.x!==void 0)(i[0]!==e.x||i[1]!==e.y||i[2]!==e.z||i[3]!==e.w)&&(s.uniform4f(this.addr,e.x,e.y,e.z,e.w),i[0]=e.x,i[1]=e.y,i[2]=e.z,i[3]=e.w);else{if(Mn(i,e))return;s.uniform4fv(this.addr,e),yn(i,e)}}function F1(s,e){const i=this.cache,r=e.elements;if(r===void 0){if(Mn(i,e))return;s.uniformMatrix2fv(this.addr,!1,e),yn(i,e)}else{if(Mn(i,r))return;O_.set(r),s.uniformMatrix2fv(this.addr,!1,O_),yn(i,r)}}function B1(s,e){const i=this.cache,r=e.elements;if(r===void 0){if(Mn(i,e))return;s.uniformMatrix3fv(this.addr,!1,e),yn(i,e)}else{if(Mn(i,r))return;N_.set(r),s.uniformMatrix3fv(this.addr,!1,N_),yn(i,r)}}function I1(s,e){const i=this.cache,r=e.elements;if(r===void 0){if(Mn(i,e))return;s.uniformMatrix4fv(this.addr,!1,e),yn(i,e)}else{if(Mn(i,r))return;L_.set(r),s.uniformMatrix4fv(this.addr,!1,L_),yn(i,r)}}function H1(s,e){const i=this.cache;i[0]!==e&&(s.uniform1i(this.addr,e),i[0]=e)}function G1(s,e){const i=this.cache;if(e.x!==void 0)(i[0]!==e.x||i[1]!==e.y)&&(s.uniform2i(this.addr,e.x,e.y),i[0]=e.x,i[1]=e.y);else{if(Mn(i,e))return;s.uniform2iv(this.addr,e),yn(i,e)}}function V1(s,e){const i=this.cache;if(e.x!==void 0)(i[0]!==e.x||i[1]!==e.y||i[2]!==e.z)&&(s.uniform3i(this.addr,e.x,e.y,e.z),i[0]=e.x,i[1]=e.y,i[2]=e.z);else{if(Mn(i,e))return;s.uniform3iv(this.addr,e),yn(i,e)}}function k1(s,e){const i=this.cache;if(e.x!==void 0)(i[0]!==e.x||i[1]!==e.y||i[2]!==e.z||i[3]!==e.w)&&(s.uniform4i(this.addr,e.x,e.y,e.z,e.w),i[0]=e.x,i[1]=e.y,i[2]=e.z,i[3]=e.w);else{if(Mn(i,e))return;s.uniform4iv(this.addr,e),yn(i,e)}}function X1(s,e){const i=this.cache;i[0]!==e&&(s.uniform1ui(this.addr,e),i[0]=e)}function W1(s,e){const i=this.cache;if(e.x!==void 0)(i[0]!==e.x||i[1]!==e.y)&&(s.uniform2ui(this.addr,e.x,e.y),i[0]=e.x,i[1]=e.y);else{if(Mn(i,e))return;s.uniform2uiv(this.addr,e),yn(i,e)}}function q1(s,e){const i=this.cache;if(e.x!==void 0)(i[0]!==e.x||i[1]!==e.y||i[2]!==e.z)&&(s.uniform3ui(this.addr,e.x,e.y,e.z),i[0]=e.x,i[1]=e.y,i[2]=e.z);else{if(Mn(i,e))return;s.uniform3uiv(this.addr,e),yn(i,e)}}function Y1(s,e){const i=this.cache;if(e.x!==void 0)(i[0]!==e.x||i[1]!==e.y||i[2]!==e.z||i[3]!==e.w)&&(s.uniform4ui(this.addr,e.x,e.y,e.z,e.w),i[0]=e.x,i[1]=e.y,i[2]=e.z,i[3]=e.w);else{if(Mn(i,e))return;s.uniform4uiv(this.addr,e),yn(i,e)}}function Z1(s,e,i){const r=this.cache,l=i.allocateTextureUnit();r[0]!==l&&(s.uniform1i(this.addr,l),r[0]=l);let u;this.type===s.SAMPLER_2D_SHADOW?(zh.compareFunction=i.isReversedDepthBuffer()?qh:Wh,u=zh):u=Pv,i.setTexture2D(e||u,l)}function K1(s,e,i){const r=this.cache,l=i.allocateTextureUnit();r[0]!==l&&(s.uniform1i(this.addr,l),r[0]=l),i.setTexture3D(e||Fv,l)}function Q1(s,e,i){const r=this.cache,l=i.allocateTextureUnit();r[0]!==l&&(s.uniform1i(this.addr,l),r[0]=l),i.setTextureCube(e||Bv,l)}function J1(s,e,i){const r=this.cache,l=i.allocateTextureUnit();r[0]!==l&&(s.uniform1i(this.addr,l),r[0]=l),i.setTexture2DArray(e||zv,l)}function $1(s){switch(s){case 5126:return N1;case 35664:return O1;case 35665:return P1;case 35666:return z1;case 35674:return F1;case 35675:return B1;case 35676:return I1;case 5124:case 35670:return H1;case 35667:case 35671:return G1;case 35668:case 35672:return V1;case 35669:case 35673:return k1;case 5125:return X1;case 36294:return W1;case 36295:return q1;case 36296:return Y1;case 35678:case 36198:case 36298:case 36306:case 35682:return Z1;case 35679:case 36299:case 36307:return K1;case 35680:case 36300:case 36308:case 36293:return Q1;case 36289:case 36303:case 36311:case 36292:return J1}}function j1(s,e){s.uniform1fv(this.addr,e)}function eA(s,e){const i=qr(e,this.size,2);s.uniform2fv(this.addr,i)}function tA(s,e){const i=qr(e,this.size,3);s.uniform3fv(this.addr,i)}function nA(s,e){const i=qr(e,this.size,4);s.uniform4fv(this.addr,i)}function iA(s,e){const i=qr(e,this.size,4);s.uniformMatrix2fv(this.addr,!1,i)}function aA(s,e){const i=qr(e,this.size,9);s.uniformMatrix3fv(this.addr,!1,i)}function sA(s,e){const i=qr(e,this.size,16);s.uniformMatrix4fv(this.addr,!1,i)}function rA(s,e){s.uniform1iv(this.addr,e)}function oA(s,e){s.uniform2iv(this.addr,e)}function lA(s,e){s.uniform3iv(this.addr,e)}function cA(s,e){s.uniform4iv(this.addr,e)}function uA(s,e){s.uniform1uiv(this.addr,e)}function fA(s,e){s.uniform2uiv(this.addr,e)}function dA(s,e){s.uniform3uiv(this.addr,e)}function hA(s,e){s.uniform4uiv(this.addr,e)}function pA(s,e,i){const r=this.cache,l=e.length,u=iu(i,l);Mn(r,u)||(s.uniform1iv(this.addr,u),yn(r,u));let d;this.type===s.SAMPLER_2D_SHADOW?d=zh:d=Pv;for(let h=0;h!==l;++h)i.setTexture2D(e[h]||d,u[h])}function mA(s,e,i){const r=this.cache,l=e.length,u=iu(i,l);Mn(r,u)||(s.uniform1iv(this.addr,u),yn(r,u));for(let d=0;d!==l;++d)i.setTexture3D(e[d]||Fv,u[d])}function gA(s,e,i){const r=this.cache,l=e.length,u=iu(i,l);Mn(r,u)||(s.uniform1iv(this.addr,u),yn(r,u));for(let d=0;d!==l;++d)i.setTextureCube(e[d]||Bv,u[d])}function _A(s,e,i){const r=this.cache,l=e.length,u=iu(i,l);Mn(r,u)||(s.uniform1iv(this.addr,u),yn(r,u));for(let d=0;d!==l;++d)i.setTexture2DArray(e[d]||zv,u[d])}function vA(s){switch(s){case 5126:return j1;case 35664:return eA;case 35665:return tA;case 35666:return nA;case 35674:return iA;case 35675:return aA;case 35676:return sA;case 5124:case 35670:return rA;case 35667:case 35671:return oA;case 35668:case 35672:return lA;case 35669:case 35673:return cA;case 5125:return uA;case 36294:return fA;case 36295:return dA;case 36296:return hA;case 35678:case 36198:case 36298:case 36306:case 35682:return pA;case 35679:case 36299:case 36307:return mA;case 35680:case 36300:case 36308:case 36293:return gA;case 36289:case 36303:case 36311:case 36292:return _A}}class xA{constructor(e,i,r){this.id=e,this.addr=r,this.cache=[],this.type=i.type,this.setValue=$1(i.type)}}class SA{constructor(e,i,r){this.id=e,this.addr=r,this.cache=[],this.type=i.type,this.size=i.size,this.setValue=vA(i.type)}}class MA{constructor(e){this.id=e,this.seq=[],this.map={}}setValue(e,i,r){const l=this.seq;for(let u=0,d=l.length;u!==d;++u){const h=l[u];h.setValue(e,i[h.id],r)}}}const Gd=/(\w+)(\])?(\[|\.)?/g;function P_(s,e){s.seq.push(e),s.map[e.id]=e}function yA(s,e,i){const r=s.name,l=r.length;for(Gd.lastIndex=0;;){const u=Gd.exec(r),d=Gd.lastIndex;let h=u[1];const m=u[2]==="]",p=u[3];if(m&&(h=h|0),p===void 0||p==="["&&d+2===l){P_(i,p===void 0?new xA(h,s,e):new SA(h,s,e));break}else{let x=i.map[h];x===void 0&&(x=new MA(h),P_(i,x)),i=x}}}class qc{constructor(e,i){this.seq=[],this.map={};const r=e.getProgramParameter(i,e.ACTIVE_UNIFORMS);for(let d=0;d<r;++d){const h=e.getActiveUniform(i,d),m=e.getUniformLocation(i,h.name);yA(h,m,this)}const l=[],u=[];for(const d of this.seq)d.type===e.SAMPLER_2D_SHADOW||d.type===e.SAMPLER_CUBE_SHADOW||d.type===e.SAMPLER_2D_ARRAY_SHADOW?l.push(d):u.push(d);l.length>0&&(this.seq=l.concat(u))}setValue(e,i,r,l){const u=this.map[i];u!==void 0&&u.setValue(e,r,l)}setOptional(e,i,r){const l=i[r];l!==void 0&&this.setValue(e,r,l)}static upload(e,i,r,l){for(let u=0,d=i.length;u!==d;++u){const h=i[u],m=r[h.id];m.needsUpdate!==!1&&h.setValue(e,m.value,l)}}static seqWithValue(e,i){const r=[];for(let l=0,u=e.length;l!==u;++l){const d=e[l];d.id in i&&r.push(d)}return r}}function z_(s,e,i){const r=s.createShader(e);return s.shaderSource(r,i),s.compileShader(r),r}const EA=37297;let bA=0;function TA(s,e){const i=s.split(`
`),r=[],l=Math.max(e-6,0),u=Math.min(e+6,i.length);for(let d=l;d<u;d++){const h=d+1;r.push(`${h===e?">":" "} ${h}: ${i[d]}`)}return r.join(`
`)}const F_=new st;function AA(s){yt._getMatrix(F_,yt.workingColorSpace,s);const e=`mat3( ${F_.elements.map(i=>i.toFixed(4))} )`;switch(yt.getTransfer(s)){case Qc:return[e,"LinearTransferOETF"];case It:return[e,"sRGBTransferOETF"];default:return et("WebGLProgram: Unsupported color space: ",s),[e,"LinearTransferOETF"]}}function B_(s,e,i){const r=s.getShaderParameter(e,s.COMPILE_STATUS),u=(s.getShaderInfoLog(e)||"").trim();if(r&&u==="")return"";const d=/ERROR: 0:(\d+)/.exec(u);if(d){const h=parseInt(d[1]);return i.toUpperCase()+`

`+u+`

`+TA(s.getShaderSource(e),h)}else return u}function RA(s,e){const i=AA(e);return[`vec4 ${s}( vec4 value ) {`,`	return ${i[1]}( vec4( value.rgb * ${i[0]}, value.a ) );`,"}"].join(`
`)}const CA={[iv]:"Linear",[av]:"Reinhard",[sv]:"Cineon",[rv]:"ACESFilmic",[lv]:"AgX",[cv]:"Neutral",[ov]:"Custom"};function wA(s,e){const i=CA[e];return i===void 0?(et("WebGLProgram: Unsupported toneMapping:",e),"vec3 "+s+"( vec3 color ) { return LinearToneMapping( color ); }"):"vec3 "+s+"( vec3 color ) { return "+i+"ToneMapping( color ); }"}const Hc=new ae;function DA(){yt.getLuminanceCoefficients(Hc);const s=Hc.x.toFixed(4),e=Hc.y.toFixed(4),i=Hc.z.toFixed(4);return["float luminance( const in vec3 rgb ) {",`	const vec3 weights = vec3( ${s}, ${e}, ${i} );`,"	return dot( weights, rgb );","}"].join(`
`)}function UA(s){return[s.extensionClipCullDistance?"#extension GL_ANGLE_clip_cull_distance : require":"",s.extensionMultiDraw?"#extension GL_ANGLE_multi_draw : require":""].filter(Zo).join(`
`)}function LA(s){const e=[];for(const i in s){const r=s[i];r!==!1&&e.push("#define "+i+" "+r)}return e.join(`
`)}function NA(s,e){const i={},r=s.getProgramParameter(e,s.ACTIVE_ATTRIBUTES);for(let l=0;l<r;l++){const u=s.getActiveAttrib(e,l),d=u.name;let h=1;u.type===s.FLOAT_MAT2&&(h=2),u.type===s.FLOAT_MAT3&&(h=3),u.type===s.FLOAT_MAT4&&(h=4),i[d]={type:u.type,location:s.getAttribLocation(e,d),locationSize:h}}return i}function Zo(s){return s!==""}function I_(s,e){const i=e.numSpotLightShadows+e.numSpotLightMaps-e.numSpotLightShadowsWithMaps;return s.replace(/NUM_DIR_LIGHTS/g,e.numDirLights).replace(/NUM_SPOT_LIGHTS/g,e.numSpotLights).replace(/NUM_SPOT_LIGHT_MAPS/g,e.numSpotLightMaps).replace(/NUM_SPOT_LIGHT_COORDS/g,i).replace(/NUM_RECT_AREA_LIGHTS/g,e.numRectAreaLights).replace(/NUM_POINT_LIGHTS/g,e.numPointLights).replace(/NUM_HEMI_LIGHTS/g,e.numHemiLights).replace(/NUM_DIR_LIGHT_SHADOWS/g,e.numDirLightShadows).replace(/NUM_SPOT_LIGHT_SHADOWS_WITH_MAPS/g,e.numSpotLightShadowsWithMaps).replace(/NUM_SPOT_LIGHT_SHADOWS/g,e.numSpotLightShadows).replace(/NUM_POINT_LIGHT_SHADOWS/g,e.numPointLightShadows)}function H_(s,e){return s.replace(/NUM_CLIPPING_PLANES/g,e.numClippingPlanes).replace(/UNION_CLIPPING_PLANES/g,e.numClippingPlanes-e.numClipIntersection)}const OA=/^[ \t]*#include +<([\w\d./]+)>/gm;function Fh(s){return s.replace(OA,zA)}const PA=new Map;function zA(s,e){let i=ct[e];if(i===void 0){const r=PA.get(e);if(r!==void 0)i=ct[r],et('WebGLRenderer: Shader chunk "%s" has been deprecated. Use "%s" instead.',e,r);else throw new Error("THREE.WebGLProgram: Can not resolve #include <"+e+">")}return Fh(i)}const FA=/#pragma unroll_loop_start\s+for\s*\(\s*int\s+i\s*=\s*(\d+)\s*;\s*i\s*<\s*(\d+)\s*;\s*i\s*\+\+\s*\)\s*{([\s\S]+?)}\s+#pragma unroll_loop_end/g;function G_(s){return s.replace(FA,BA)}function BA(s,e,i,r){let l="";for(let u=parseInt(e);u<parseInt(i);u++)l+=r.replace(/\[\s*i\s*\]/g,"[ "+u+" ]").replace(/UNROLLED_LOOP_INDEX/g,u);return l}function V_(s){let e=`precision ${s.precision} float;
	precision ${s.precision} int;
	precision ${s.precision} sampler2D;
	precision ${s.precision} samplerCube;
	precision ${s.precision} sampler3D;
	precision ${s.precision} sampler2DArray;
	precision ${s.precision} sampler2DShadow;
	precision ${s.precision} samplerCubeShadow;
	precision ${s.precision} sampler2DArrayShadow;
	precision ${s.precision} isampler2D;
	precision ${s.precision} isampler3D;
	precision ${s.precision} isamplerCube;
	precision ${s.precision} isampler2DArray;
	precision ${s.precision} usampler2D;
	precision ${s.precision} usampler3D;
	precision ${s.precision} usamplerCube;
	precision ${s.precision} usampler2DArray;
	`;return s.precision==="highp"?e+=`
#define HIGH_PRECISION`:s.precision==="mediump"?e+=`
#define MEDIUM_PRECISION`:s.precision==="lowp"&&(e+=`
#define LOW_PRECISION`),e}const IA={[Gc]:"SHADOWMAP_TYPE_PCF",[Yo]:"SHADOWMAP_TYPE_VSM"};function HA(s){return IA[s.shadowMapType]||"SHADOWMAP_TYPE_BASIC"}const GA={[Is]:"ENVMAP_TYPE_CUBE",[Gr]:"ENVMAP_TYPE_CUBE",[eu]:"ENVMAP_TYPE_CUBE_UV"};function VA(s){return s.envMap===!1?"ENVMAP_TYPE_CUBE":GA[s.envMapMode]||"ENVMAP_TYPE_CUBE"}const kA={[Gr]:"ENVMAP_MODE_REFRACTION"};function XA(s){return s.envMap===!1?"ENVMAP_MODE_REFLECTION":kA[s.envMapMode]||"ENVMAP_MODE_REFLECTION"}const WA={[nv]:"ENVMAP_BLENDING_MULTIPLY",[xy]:"ENVMAP_BLENDING_MIX",[Sy]:"ENVMAP_BLENDING_ADD"};function qA(s){return s.envMap===!1?"ENVMAP_BLENDING_NONE":WA[s.combine]||"ENVMAP_BLENDING_NONE"}function YA(s){const e=s.envMapCubeUVHeight;if(e===null)return null;const i=Math.log2(e)-2,r=1/e;return{texelWidth:1/(3*Math.max(Math.pow(2,i),112)),texelHeight:r,maxMip:i}}function ZA(s,e,i,r){const l=s.getContext(),u=i.defines;let d=i.vertexShader,h=i.fragmentShader;const m=HA(i),p=VA(i),v=XA(i),x=qA(i),g=YA(i),E=UA(i),T=LA(u),D=l.createProgram();let y,S,z=i.glslVersion?"#version "+i.glslVersion+`
`:"";i.isRawShaderMaterial?(y=["#define SHADER_TYPE "+i.shaderType,"#define SHADER_NAME "+i.shaderName,T].filter(Zo).join(`
`),y.length>0&&(y+=`
`),S=["#define SHADER_TYPE "+i.shaderType,"#define SHADER_NAME "+i.shaderName,T].filter(Zo).join(`
`),S.length>0&&(S+=`
`)):(y=[V_(i),"#define SHADER_TYPE "+i.shaderType,"#define SHADER_NAME "+i.shaderName,T,i.extensionClipCullDistance?"#define USE_CLIP_DISTANCE":"",i.batching?"#define USE_BATCHING":"",i.batchingColor?"#define USE_BATCHING_COLOR":"",i.instancing?"#define USE_INSTANCING":"",i.instancingColor?"#define USE_INSTANCING_COLOR":"",i.instancingMorph?"#define USE_INSTANCING_MORPH":"",i.useFog&&i.fog?"#define USE_FOG":"",i.useFog&&i.fogExp2?"#define FOG_EXP2":"",i.map?"#define USE_MAP":"",i.envMap?"#define USE_ENVMAP":"",i.envMap?"#define "+v:"",i.lightMap?"#define USE_LIGHTMAP":"",i.aoMap?"#define USE_AOMAP":"",i.bumpMap?"#define USE_BUMPMAP":"",i.normalMap?"#define USE_NORMALMAP":"",i.normalMapObjectSpace?"#define USE_NORMALMAP_OBJECTSPACE":"",i.normalMapTangentSpace?"#define USE_NORMALMAP_TANGENTSPACE":"",i.displacementMap?"#define USE_DISPLACEMENTMAP":"",i.emissiveMap?"#define USE_EMISSIVEMAP":"",i.anisotropy?"#define USE_ANISOTROPY":"",i.anisotropyMap?"#define USE_ANISOTROPYMAP":"",i.clearcoatMap?"#define USE_CLEARCOATMAP":"",i.clearcoatRoughnessMap?"#define USE_CLEARCOAT_ROUGHNESSMAP":"",i.clearcoatNormalMap?"#define USE_CLEARCOAT_NORMALMAP":"",i.iridescenceMap?"#define USE_IRIDESCENCEMAP":"",i.iridescenceThicknessMap?"#define USE_IRIDESCENCE_THICKNESSMAP":"",i.specularMap?"#define USE_SPECULARMAP":"",i.specularColorMap?"#define USE_SPECULAR_COLORMAP":"",i.specularIntensityMap?"#define USE_SPECULAR_INTENSITYMAP":"",i.roughnessMap?"#define USE_ROUGHNESSMAP":"",i.metalnessMap?"#define USE_METALNESSMAP":"",i.alphaMap?"#define USE_ALPHAMAP":"",i.alphaHash?"#define USE_ALPHAHASH":"",i.transmission?"#define USE_TRANSMISSION":"",i.transmissionMap?"#define USE_TRANSMISSIONMAP":"",i.thicknessMap?"#define USE_THICKNESSMAP":"",i.sheenColorMap?"#define USE_SHEEN_COLORMAP":"",i.sheenRoughnessMap?"#define USE_SHEEN_ROUGHNESSMAP":"",i.mapUv?"#define MAP_UV "+i.mapUv:"",i.alphaMapUv?"#define ALPHAMAP_UV "+i.alphaMapUv:"",i.lightMapUv?"#define LIGHTMAP_UV "+i.lightMapUv:"",i.aoMapUv?"#define AOMAP_UV "+i.aoMapUv:"",i.emissiveMapUv?"#define EMISSIVEMAP_UV "+i.emissiveMapUv:"",i.bumpMapUv?"#define BUMPMAP_UV "+i.bumpMapUv:"",i.normalMapUv?"#define NORMALMAP_UV "+i.normalMapUv:"",i.displacementMapUv?"#define DISPLACEMENTMAP_UV "+i.displacementMapUv:"",i.metalnessMapUv?"#define METALNESSMAP_UV "+i.metalnessMapUv:"",i.roughnessMapUv?"#define ROUGHNESSMAP_UV "+i.roughnessMapUv:"",i.anisotropyMapUv?"#define ANISOTROPYMAP_UV "+i.anisotropyMapUv:"",i.clearcoatMapUv?"#define CLEARCOATMAP_UV "+i.clearcoatMapUv:"",i.clearcoatNormalMapUv?"#define CLEARCOAT_NORMALMAP_UV "+i.clearcoatNormalMapUv:"",i.clearcoatRoughnessMapUv?"#define CLEARCOAT_ROUGHNESSMAP_UV "+i.clearcoatRoughnessMapUv:"",i.iridescenceMapUv?"#define IRIDESCENCEMAP_UV "+i.iridescenceMapUv:"",i.iridescenceThicknessMapUv?"#define IRIDESCENCE_THICKNESSMAP_UV "+i.iridescenceThicknessMapUv:"",i.sheenColorMapUv?"#define SHEEN_COLORMAP_UV "+i.sheenColorMapUv:"",i.sheenRoughnessMapUv?"#define SHEEN_ROUGHNESSMAP_UV "+i.sheenRoughnessMapUv:"",i.specularMapUv?"#define SPECULARMAP_UV "+i.specularMapUv:"",i.specularColorMapUv?"#define SPECULAR_COLORMAP_UV "+i.specularColorMapUv:"",i.specularIntensityMapUv?"#define SPECULAR_INTENSITYMAP_UV "+i.specularIntensityMapUv:"",i.transmissionMapUv?"#define TRANSMISSIONMAP_UV "+i.transmissionMapUv:"",i.thicknessMapUv?"#define THICKNESSMAP_UV "+i.thicknessMapUv:"",i.vertexTangents&&i.flatShading===!1?"#define USE_TANGENT":"",i.vertexNormals?"#define HAS_NORMAL":"",i.vertexColors?"#define USE_COLOR":"",i.vertexAlphas?"#define USE_COLOR_ALPHA":"",i.vertexUv1s?"#define USE_UV1":"",i.vertexUv2s?"#define USE_UV2":"",i.vertexUv3s?"#define USE_UV3":"",i.pointsUvs?"#define USE_POINTS_UV":"",i.flatShading?"#define FLAT_SHADED":"",i.skinning?"#define USE_SKINNING":"",i.morphTargets?"#define USE_MORPHTARGETS":"",i.morphNormals&&i.flatShading===!1?"#define USE_MORPHNORMALS":"",i.morphColors?"#define USE_MORPHCOLORS":"",i.morphTargetsCount>0?"#define MORPHTARGETS_TEXTURE_STRIDE "+i.morphTextureStride:"",i.morphTargetsCount>0?"#define MORPHTARGETS_COUNT "+i.morphTargetsCount:"",i.doubleSided?"#define DOUBLE_SIDED":"",i.flipSided?"#define FLIP_SIDED":"",i.shadowMapEnabled?"#define USE_SHADOWMAP":"",i.shadowMapEnabled?"#define "+m:"",i.sizeAttenuation?"#define USE_SIZEATTENUATION":"",i.numLightProbes>0?"#define USE_LIGHT_PROBES":"",i.logarithmicDepthBuffer?"#define USE_LOGARITHMIC_DEPTH_BUFFER":"",i.reversedDepthBuffer?"#define USE_REVERSED_DEPTH_BUFFER":"","uniform mat4 modelMatrix;","uniform mat4 modelViewMatrix;","uniform mat4 projectionMatrix;","uniform mat4 viewMatrix;","uniform mat3 normalMatrix;","uniform vec3 cameraPosition;","uniform bool isOrthographic;","#ifdef USE_INSTANCING","	attribute mat4 instanceMatrix;","#endif","#ifdef USE_INSTANCING_COLOR","	attribute vec3 instanceColor;","#endif","#ifdef USE_INSTANCING_MORPH","	uniform sampler2D morphTexture;","#endif","attribute vec3 position;","attribute vec3 normal;","attribute vec2 uv;","#ifdef USE_UV1","	attribute vec2 uv1;","#endif","#ifdef USE_UV2","	attribute vec2 uv2;","#endif","#ifdef USE_UV3","	attribute vec2 uv3;","#endif","#ifdef USE_TANGENT","	attribute vec4 tangent;","#endif","#if defined( USE_COLOR_ALPHA )","	attribute vec4 color;","#elif defined( USE_COLOR )","	attribute vec3 color;","#endif","#ifdef USE_SKINNING","	attribute vec4 skinIndex;","	attribute vec4 skinWeight;","#endif",`
`].filter(Zo).join(`
`),S=[V_(i),"#define SHADER_TYPE "+i.shaderType,"#define SHADER_NAME "+i.shaderName,T,i.useFog&&i.fog?"#define USE_FOG":"",i.useFog&&i.fogExp2?"#define FOG_EXP2":"",i.alphaToCoverage?"#define ALPHA_TO_COVERAGE":"",i.map?"#define USE_MAP":"",i.matcap?"#define USE_MATCAP":"",i.envMap?"#define USE_ENVMAP":"",i.envMap?"#define "+p:"",i.envMap?"#define "+v:"",i.envMap?"#define "+x:"",g?"#define CUBEUV_TEXEL_WIDTH "+g.texelWidth:"",g?"#define CUBEUV_TEXEL_HEIGHT "+g.texelHeight:"",g?"#define CUBEUV_MAX_MIP "+g.maxMip+".0":"",i.lightMap?"#define USE_LIGHTMAP":"",i.aoMap?"#define USE_AOMAP":"",i.bumpMap?"#define USE_BUMPMAP":"",i.normalMap?"#define USE_NORMALMAP":"",i.normalMapObjectSpace?"#define USE_NORMALMAP_OBJECTSPACE":"",i.normalMapTangentSpace?"#define USE_NORMALMAP_TANGENTSPACE":"",i.packedNormalMap?"#define USE_PACKED_NORMALMAP":"",i.emissiveMap?"#define USE_EMISSIVEMAP":"",i.anisotropy?"#define USE_ANISOTROPY":"",i.anisotropyMap?"#define USE_ANISOTROPYMAP":"",i.clearcoat?"#define USE_CLEARCOAT":"",i.clearcoatMap?"#define USE_CLEARCOATMAP":"",i.clearcoatRoughnessMap?"#define USE_CLEARCOAT_ROUGHNESSMAP":"",i.clearcoatNormalMap?"#define USE_CLEARCOAT_NORMALMAP":"",i.dispersion?"#define USE_DISPERSION":"",i.iridescence?"#define USE_IRIDESCENCE":"",i.iridescenceMap?"#define USE_IRIDESCENCEMAP":"",i.iridescenceThicknessMap?"#define USE_IRIDESCENCE_THICKNESSMAP":"",i.specularMap?"#define USE_SPECULARMAP":"",i.specularColorMap?"#define USE_SPECULAR_COLORMAP":"",i.specularIntensityMap?"#define USE_SPECULAR_INTENSITYMAP":"",i.roughnessMap?"#define USE_ROUGHNESSMAP":"",i.metalnessMap?"#define USE_METALNESSMAP":"",i.alphaMap?"#define USE_ALPHAMAP":"",i.alphaTest?"#define USE_ALPHATEST":"",i.alphaHash?"#define USE_ALPHAHASH":"",i.sheen?"#define USE_SHEEN":"",i.sheenColorMap?"#define USE_SHEEN_COLORMAP":"",i.sheenRoughnessMap?"#define USE_SHEEN_ROUGHNESSMAP":"",i.transmission?"#define USE_TRANSMISSION":"",i.transmissionMap?"#define USE_TRANSMISSIONMAP":"",i.thicknessMap?"#define USE_THICKNESSMAP":"",i.vertexTangents&&i.flatShading===!1?"#define USE_TANGENT":"",i.vertexColors||i.instancingColor?"#define USE_COLOR":"",i.vertexAlphas||i.batchingColor?"#define USE_COLOR_ALPHA":"",i.vertexUv1s?"#define USE_UV1":"",i.vertexUv2s?"#define USE_UV2":"",i.vertexUv3s?"#define USE_UV3":"",i.pointsUvs?"#define USE_POINTS_UV":"",i.gradientMap?"#define USE_GRADIENTMAP":"",i.flatShading?"#define FLAT_SHADED":"",i.doubleSided?"#define DOUBLE_SIDED":"",i.flipSided?"#define FLIP_SIDED":"",i.shadowMapEnabled?"#define USE_SHADOWMAP":"",i.shadowMapEnabled?"#define "+m:"",i.premultipliedAlpha?"#define PREMULTIPLIED_ALPHA":"",i.numLightProbes>0?"#define USE_LIGHT_PROBES":"",i.numLightProbeGrids>0?"#define USE_LIGHT_PROBES_GRID":"",i.decodeVideoTexture?"#define DECODE_VIDEO_TEXTURE":"",i.decodeVideoTextureEmissive?"#define DECODE_VIDEO_TEXTURE_EMISSIVE":"",i.logarithmicDepthBuffer?"#define USE_LOGARITHMIC_DEPTH_BUFFER":"",i.reversedDepthBuffer?"#define USE_REVERSED_DEPTH_BUFFER":"","uniform mat4 viewMatrix;","uniform vec3 cameraPosition;","uniform bool isOrthographic;",i.toneMapping!==Xi?"#define TONE_MAPPING":"",i.toneMapping!==Xi?ct.tonemapping_pars_fragment:"",i.toneMapping!==Xi?wA("toneMapping",i.toneMapping):"",i.dithering?"#define DITHERING":"",i.opaque?"#define OPAQUE":"",ct.colorspace_pars_fragment,RA("linearToOutputTexel",i.outputColorSpace),DA(),i.useDepthPacking?"#define DEPTH_PACKING "+i.depthPacking:"",`
`].filter(Zo).join(`
`)),d=Fh(d),d=I_(d,i),d=H_(d,i),h=Fh(h),h=I_(h,i),h=H_(h,i),d=G_(d),h=G_(h),i.isRawShaderMaterial!==!0&&(z=`#version 300 es
`,y=[E,"#define attribute in","#define varying out","#define texture2D texture"].join(`
`)+`
`+y,S=["#define varying in",i.glslVersion===$0?"":"layout(location = 0) out highp vec4 pc_fragColor;",i.glslVersion===$0?"":"#define gl_FragColor pc_fragColor","#define gl_FragDepthEXT gl_FragDepth","#define texture2D texture","#define textureCube texture","#define texture2DProj textureProj","#define texture2DLodEXT textureLod","#define texture2DProjLodEXT textureProjLod","#define textureCubeLodEXT textureLod","#define texture2DGradEXT textureGrad","#define texture2DProjGradEXT textureProjGrad","#define textureCubeGradEXT textureGrad"].join(`
`)+`
`+S);const F=z+y+d,C=z+S+h,O=z_(l,l.VERTEX_SHADER,F),U=z_(l,l.FRAGMENT_SHADER,C);l.attachShader(D,O),l.attachShader(D,U),i.index0AttributeName!==void 0?l.bindAttribLocation(D,0,i.index0AttributeName):i.hasPositionAttribute===!0&&l.bindAttribLocation(D,0,"position"),l.linkProgram(D);function B(V){if(s.debug.checkShaderErrors){const Z=l.getProgramInfoLog(D)||"",de=l.getShaderInfoLog(O)||"",he=l.getShaderInfoLog(U)||"",K=Z.trim(),N=de.trim(),I=he.trim();let $=!0,se=!0;if(l.getProgramParameter(D,l.LINK_STATUS)===!1)if($=!1,typeof s.debug.onShaderError=="function")s.debug.onShaderError(l,D,O,U);else{const pe=B_(l,O,"vertex"),xe=B_(l,U,"fragment");bt("WebGLProgram: Shader Error "+l.getError()+" - VALIDATE_STATUS "+l.getProgramParameter(D,l.VALIDATE_STATUS)+`

Material Name: `+V.name+`
Material Type: `+V.type+`

Program Info Log: `+K+`
`+pe+`
`+xe)}else K!==""?et("WebGLProgram: Program Info Log:",K):(N===""||I==="")&&(se=!1);se&&(V.diagnostics={runnable:$,programLog:K,vertexShader:{log:N,prefix:y},fragmentShader:{log:I,prefix:S}})}l.deleteShader(O),l.deleteShader(U),b=new qc(l,D),L=NA(l,D)}let b;this.getUniforms=function(){return b===void 0&&B(this),b};let L;this.getAttributes=function(){return L===void 0&&B(this),L};let W=i.rendererExtensionParallelShaderCompile===!1;return this.isReady=function(){return W===!1&&(W=l.getProgramParameter(D,EA)),W},this.destroy=function(){r.releaseStatesOfProgram(this),l.deleteProgram(D),this.program=void 0},this.type=i.shaderType,this.name=i.shaderName,this.id=bA++,this.cacheKey=e,this.usedTimes=1,this.program=D,this.vertexShader=O,this.fragmentShader=U,this}let KA=0;class QA{constructor(){this.shaderCache=new Map,this.materialCache=new Map}update(e,i,r){const l=this._getShaderCacheForMaterial(e);return l.has(i)===!1&&(l.add(i),i.usedTimes++),l.has(r)===!1&&(l.add(r),r.usedTimes++),this}remove(e){const i=this.materialCache.get(e);for(const r of i)r.usedTimes--,r.usedTimes===0&&this.shaderCache.delete(r.code);return this.materialCache.delete(e),this}getVertexShaderStage(e){return this._getShaderStage(e.vertexShader)}getFragmentShaderStage(e){return this._getShaderStage(e.fragmentShader)}dispose(){this.shaderCache.clear(),this.materialCache.clear()}_getShaderCacheForMaterial(e){const i=this.materialCache;let r=i.get(e);return r===void 0&&(r=new Set,i.set(e,r)),r}_getShaderStage(e){const i=this.shaderCache;let r=i.get(e);return r===void 0&&(r=new JA(e),i.set(e,r)),r}}class JA{constructor(e){this.id=KA++,this.code=e,this.usedTimes=0}}function $A(s){return s===Hs||s===Yc||s===Zc}function jA(s,e,i,r,l,u){const d=new Sv,h=new QA,m=new Set,p=[],v=new Map,x=r.logarithmicDepthBuffer;let g=r.precision;const E={MeshDepthMaterial:"depth",MeshDistanceMaterial:"distance",MeshNormalMaterial:"normal",MeshBasicMaterial:"basic",MeshLambertMaterial:"lambert",MeshPhongMaterial:"phong",MeshToonMaterial:"toon",MeshStandardMaterial:"physical",MeshPhysicalMaterial:"physical",MeshMatcapMaterial:"matcap",LineBasicMaterial:"basic",LineDashedMaterial:"dashed",PointsMaterial:"points",ShadowMaterial:"shadow",SpriteMaterial:"sprite"};function T(b){return m.add(b),b===0?"uv":`uv${b}`}function D(b,L,W,V,Z,de){const he=V.fog,K=Z.geometry,N=b.isMeshStandardMaterial||b.isMeshLambertMaterial||b.isMeshPhongMaterial?V.environment:null,I=b.isMeshStandardMaterial||b.isMeshLambertMaterial&&!b.envMap||b.isMeshPhongMaterial&&!b.envMap,$=e.get(b.envMap||N,I),se=$&&$.mapping===eu?$.image.height:null,pe=E[b.type];b.precision!==null&&(g=r.getMaxPrecision(b.precision),g!==b.precision&&et("WebGLProgram.getParameters:",b.precision,"not supported, using",g,"instead."));const xe=K.morphAttributes.position||K.morphAttributes.normal||K.morphAttributes.color,Se=xe!==void 0?xe.length:0;let Te=0;K.morphAttributes.position!==void 0&&(Te=1),K.morphAttributes.normal!==void 0&&(Te=2),K.morphAttributes.color!==void 0&&(Te=3);let tt,nt,ue,Ae;if(pe){const Ie=Gi[pe];tt=Ie.vertexShader,nt=Ie.fragmentShader}else{tt=b.vertexShader,nt=b.fragmentShader;const Ie=h.getVertexShaderStage(b),Qt=h.getFragmentShaderStage(b);h.update(b,Ie,Qt),ue=Ie.id,Ae=Qt.id}const Me=s.getRenderTarget(),$e=s.state.buffers.depth.getReversed(),at=Z.isInstancedMesh===!0,Ze=Z.isBatchedMesh===!0,Yt=!!b.map,ut=!!b.matcap,xt=!!$,St=!!b.aoMap,ft=!!b.lightMap,en=!!b.bumpMap&&b.wireframe===!1,tn=!!b.normalMap,nn=!!b.displacementMap,ln=!!b.emissiveMap,qt=!!b.metalnessMap,an=!!b.roughnessMap,X=b.anisotropy>0,Ht=b.clearcoat>0,wt=b.dispersion>0,w=b.iridescence>0,M=b.sheen>0,Y=b.transmission>0,te=X&&!!b.anisotropyMap,le=Ht&&!!b.clearcoatMap,ye=Ht&&!!b.clearcoatNormalMap,Ce=Ht&&!!b.clearcoatRoughnessMap,oe=w&&!!b.iridescenceMap,ce=w&&!!b.iridescenceThicknessMap,be=M&&!!b.sheenColorMap,Pe=M&&!!b.sheenRoughnessMap,Ue=!!b.specularMap,we=!!b.specularColorMap,Ye=!!b.specularIntensityMap,Ke=Y&&!!b.transmissionMap,it=Y&&!!b.thicknessMap,G=!!b.gradientMap,Ee=!!b.alphaMap,me=b.alphaTest>0,Re=!!b.alphaHash,Oe=!!b.extensions;let ve=Xi;b.toneMapped&&(Me===null||Me.isXRRenderTarget===!0)&&(ve=s.toneMapping);const ke={shaderID:pe,shaderType:b.type,shaderName:b.name,vertexShader:tt,fragmentShader:nt,defines:b.defines,customVertexShaderID:ue,customFragmentShaderID:Ae,isRawShaderMaterial:b.isRawShaderMaterial===!0,glslVersion:b.glslVersion,precision:g,batching:Ze,batchingColor:Ze&&Z._colorsTexture!==null,instancing:at,instancingColor:at&&Z.instanceColor!==null,instancingMorph:at&&Z.morphTexture!==null,outputColorSpace:Me===null?s.outputColorSpace:Me.isXRRenderTarget===!0?Me.texture.colorSpace:yt.workingColorSpace,alphaToCoverage:!!b.alphaToCoverage,map:Yt,matcap:ut,envMap:xt,envMapMode:xt&&$.mapping,envMapCubeUVHeight:se,aoMap:St,lightMap:ft,bumpMap:en,normalMap:tn,displacementMap:nn,emissiveMap:ln,normalMapObjectSpace:tn&&b.normalMapType===Ey,normalMapTangentSpace:tn&&b.normalMapType===K0,packedNormalMap:tn&&b.normalMapType===K0&&$A(b.normalMap.format),metalnessMap:qt,roughnessMap:an,anisotropy:X,anisotropyMap:te,clearcoat:Ht,clearcoatMap:le,clearcoatNormalMap:ye,clearcoatRoughnessMap:Ce,dispersion:wt,iridescence:w,iridescenceMap:oe,iridescenceThicknessMap:ce,sheen:M,sheenColorMap:be,sheenRoughnessMap:Pe,specularMap:Ue,specularColorMap:we,specularIntensityMap:Ye,transmission:Y,transmissionMap:Ke,thicknessMap:it,gradientMap:G,opaque:b.transparent===!1&&b.blending===Fr&&b.alphaToCoverage===!1,alphaMap:Ee,alphaTest:me,alphaHash:Re,combine:b.combine,mapUv:Yt&&T(b.map.channel),aoMapUv:St&&T(b.aoMap.channel),lightMapUv:ft&&T(b.lightMap.channel),bumpMapUv:en&&T(b.bumpMap.channel),normalMapUv:tn&&T(b.normalMap.channel),displacementMapUv:nn&&T(b.displacementMap.channel),emissiveMapUv:ln&&T(b.emissiveMap.channel),metalnessMapUv:qt&&T(b.metalnessMap.channel),roughnessMapUv:an&&T(b.roughnessMap.channel),anisotropyMapUv:te&&T(b.anisotropyMap.channel),clearcoatMapUv:le&&T(b.clearcoatMap.channel),clearcoatNormalMapUv:ye&&T(b.clearcoatNormalMap.channel),clearcoatRoughnessMapUv:Ce&&T(b.clearcoatRoughnessMap.channel),iridescenceMapUv:oe&&T(b.iridescenceMap.channel),iridescenceThicknessMapUv:ce&&T(b.iridescenceThicknessMap.channel),sheenColorMapUv:be&&T(b.sheenColorMap.channel),sheenRoughnessMapUv:Pe&&T(b.sheenRoughnessMap.channel),specularMapUv:Ue&&T(b.specularMap.channel),specularColorMapUv:we&&T(b.specularColorMap.channel),specularIntensityMapUv:Ye&&T(b.specularIntensityMap.channel),transmissionMapUv:Ke&&T(b.transmissionMap.channel),thicknessMapUv:it&&T(b.thicknessMap.channel),alphaMapUv:Ee&&T(b.alphaMap.channel),vertexTangents:!!K.attributes.tangent&&(tn||X),vertexNormals:!!K.attributes.normal,vertexColors:b.vertexColors,vertexAlphas:b.vertexColors===!0&&!!K.attributes.color&&K.attributes.color.itemSize===4,pointsUvs:Z.isPoints===!0&&!!K.attributes.uv&&(Yt||Ee),fog:!!he,useFog:b.fog===!0,fogExp2:!!he&&he.isFogExp2,flatShading:b.wireframe===!1&&(b.flatShading===!0||K.attributes.normal===void 0&&tn===!1&&(b.isMeshLambertMaterial||b.isMeshPhongMaterial||b.isMeshStandardMaterial||b.isMeshPhysicalMaterial)),sizeAttenuation:b.sizeAttenuation===!0,logarithmicDepthBuffer:x,reversedDepthBuffer:$e,skinning:Z.isSkinnedMesh===!0,hasPositionAttribute:K.attributes.position!==void 0,morphTargets:K.morphAttributes.position!==void 0,morphNormals:K.morphAttributes.normal!==void 0,morphColors:K.morphAttributes.color!==void 0,morphTargetsCount:Se,morphTextureStride:Te,numDirLights:L.directional.length,numPointLights:L.point.length,numSpotLights:L.spot.length,numSpotLightMaps:L.spotLightMap.length,numRectAreaLights:L.rectArea.length,numHemiLights:L.hemi.length,numDirLightShadows:L.directionalShadowMap.length,numPointLightShadows:L.pointShadowMap.length,numSpotLightShadows:L.spotShadowMap.length,numSpotLightShadowsWithMaps:L.numSpotLightShadowsWithMaps,numLightProbes:L.numLightProbes,numLightProbeGrids:de.length,numClippingPlanes:u.numPlanes,numClipIntersection:u.numIntersection,dithering:b.dithering,shadowMapEnabled:s.shadowMap.enabled&&W.length>0,shadowMapType:s.shadowMap.type,toneMapping:ve,decodeVideoTexture:Yt&&b.map.isVideoTexture===!0&&yt.getTransfer(b.map.colorSpace)===It,decodeVideoTextureEmissive:ln&&b.emissiveMap.isVideoTexture===!0&&yt.getTransfer(b.emissiveMap.colorSpace)===It,premultipliedAlpha:b.premultipliedAlpha,doubleSided:b.side===xa,flipSided:b.side===Yn,useDepthPacking:b.depthPacking>=0,depthPacking:b.depthPacking||0,index0AttributeName:b.index0AttributeName,extensionClipCullDistance:Oe&&b.extensions.clipCullDistance===!0&&i.has("WEBGL_clip_cull_distance"),extensionMultiDraw:(Oe&&b.extensions.multiDraw===!0||Ze)&&i.has("WEBGL_multi_draw"),rendererExtensionParallelShaderCompile:i.has("KHR_parallel_shader_compile"),customProgramCacheKey:b.customProgramCacheKey()};return ke.vertexUv1s=m.has(1),ke.vertexUv2s=m.has(2),ke.vertexUv3s=m.has(3),m.clear(),ke}function y(b){const L=[];if(b.shaderID?L.push(b.shaderID):(L.push(b.customVertexShaderID),L.push(b.customFragmentShaderID)),b.defines!==void 0)for(const W in b.defines)L.push(W),L.push(b.defines[W]);return b.isRawShaderMaterial===!1&&(S(L,b),z(L,b),L.push(s.outputColorSpace)),L.push(b.customProgramCacheKey),L.join()}function S(b,L){b.push(L.precision),b.push(L.outputColorSpace),b.push(L.envMapMode),b.push(L.envMapCubeUVHeight),b.push(L.mapUv),b.push(L.alphaMapUv),b.push(L.lightMapUv),b.push(L.aoMapUv),b.push(L.bumpMapUv),b.push(L.normalMapUv),b.push(L.displacementMapUv),b.push(L.emissiveMapUv),b.push(L.metalnessMapUv),b.push(L.roughnessMapUv),b.push(L.anisotropyMapUv),b.push(L.clearcoatMapUv),b.push(L.clearcoatNormalMapUv),b.push(L.clearcoatRoughnessMapUv),b.push(L.iridescenceMapUv),b.push(L.iridescenceThicknessMapUv),b.push(L.sheenColorMapUv),b.push(L.sheenRoughnessMapUv),b.push(L.specularMapUv),b.push(L.specularColorMapUv),b.push(L.specularIntensityMapUv),b.push(L.transmissionMapUv),b.push(L.thicknessMapUv),b.push(L.combine),b.push(L.fogExp2),b.push(L.sizeAttenuation),b.push(L.morphTargetsCount),b.push(L.morphAttributeCount),b.push(L.numDirLights),b.push(L.numPointLights),b.push(L.numSpotLights),b.push(L.numSpotLightMaps),b.push(L.numHemiLights),b.push(L.numRectAreaLights),b.push(L.numDirLightShadows),b.push(L.numPointLightShadows),b.push(L.numSpotLightShadows),b.push(L.numSpotLightShadowsWithMaps),b.push(L.numLightProbes),b.push(L.shadowMapType),b.push(L.toneMapping),b.push(L.numClippingPlanes),b.push(L.numClipIntersection),b.push(L.depthPacking)}function z(b,L){d.disableAll(),L.instancing&&d.enable(0),L.instancingColor&&d.enable(1),L.instancingMorph&&d.enable(2),L.matcap&&d.enable(3),L.envMap&&d.enable(4),L.normalMapObjectSpace&&d.enable(5),L.normalMapTangentSpace&&d.enable(6),L.clearcoat&&d.enable(7),L.iridescence&&d.enable(8),L.alphaTest&&d.enable(9),L.vertexColors&&d.enable(10),L.vertexAlphas&&d.enable(11),L.vertexUv1s&&d.enable(12),L.vertexUv2s&&d.enable(13),L.vertexUv3s&&d.enable(14),L.vertexTangents&&d.enable(15),L.anisotropy&&d.enable(16),L.alphaHash&&d.enable(17),L.batching&&d.enable(18),L.dispersion&&d.enable(19),L.batchingColor&&d.enable(20),L.gradientMap&&d.enable(21),L.packedNormalMap&&d.enable(22),L.vertexNormals&&d.enable(23),b.push(d.mask),d.disableAll(),L.fog&&d.enable(0),L.useFog&&d.enable(1),L.flatShading&&d.enable(2),L.logarithmicDepthBuffer&&d.enable(3),L.reversedDepthBuffer&&d.enable(4),L.skinning&&d.enable(5),L.morphTargets&&d.enable(6),L.morphNormals&&d.enable(7),L.morphColors&&d.enable(8),L.premultipliedAlpha&&d.enable(9),L.shadowMapEnabled&&d.enable(10),L.doubleSided&&d.enable(11),L.flipSided&&d.enable(12),L.useDepthPacking&&d.enable(13),L.dithering&&d.enable(14),L.transmission&&d.enable(15),L.sheen&&d.enable(16),L.opaque&&d.enable(17),L.pointsUvs&&d.enable(18),L.decodeVideoTexture&&d.enable(19),L.decodeVideoTextureEmissive&&d.enable(20),L.alphaToCoverage&&d.enable(21),L.numLightProbeGrids>0&&d.enable(22),L.hasPositionAttribute&&d.enable(23),b.push(d.mask)}function F(b){const L=E[b.type];let W;if(L){const V=Gi[L];W=yE.clone(V.uniforms)}else W=b.uniforms;return W}function C(b,L){let W=v.get(L);return W!==void 0?++W.usedTimes:(W=new ZA(s,L,b,l),p.push(W),v.set(L,W)),W}function O(b){if(--b.usedTimes===0){const L=p.indexOf(b);p[L]=p[p.length-1],p.pop(),v.delete(b.cacheKey),b.destroy()}}function U(b){h.remove(b)}function B(){h.dispose()}return{getParameters:D,getProgramCacheKey:y,getUniforms:F,acquireProgram:C,releaseProgram:O,releaseShaderCache:U,programs:p,dispose:B}}function eR(){let s=new WeakMap;function e(d){return s.has(d)}function i(d){let h=s.get(d);return h===void 0&&(h={},s.set(d,h)),h}function r(d){s.delete(d)}function l(d,h,m){s.get(d)[h]=m}function u(){s=new WeakMap}return{has:e,get:i,remove:r,update:l,dispose:u}}function tR(s,e){return s.groupOrder!==e.groupOrder?s.groupOrder-e.groupOrder:s.renderOrder!==e.renderOrder?s.renderOrder-e.renderOrder:s.material.id!==e.material.id?s.material.id-e.material.id:s.materialVariant!==e.materialVariant?s.materialVariant-e.materialVariant:s.z!==e.z?s.z-e.z:s.id-e.id}function k_(s,e){return s.groupOrder!==e.groupOrder?s.groupOrder-e.groupOrder:s.renderOrder!==e.renderOrder?s.renderOrder-e.renderOrder:s.z!==e.z?e.z-s.z:s.id-e.id}function X_(){const s=[];let e=0;const i=[],r=[],l=[];function u(){e=0,i.length=0,r.length=0,l.length=0}function d(g){let E=0;return g.isInstancedMesh&&(E+=2),g.isSkinnedMesh&&(E+=1),E}function h(g,E,T,D,y,S){let z=s[e];return z===void 0?(z={id:g.id,object:g,geometry:E,material:T,materialVariant:d(g),groupOrder:D,renderOrder:g.renderOrder,z:y,group:S},s[e]=z):(z.id=g.id,z.object=g,z.geometry=E,z.material=T,z.materialVariant=d(g),z.groupOrder=D,z.renderOrder=g.renderOrder,z.z=y,z.group=S),e++,z}function m(g,E,T,D,y,S){const z=h(g,E,T,D,y,S);T.transmission>0?r.push(z):T.transparent===!0?l.push(z):i.push(z)}function p(g,E,T,D,y,S){const z=h(g,E,T,D,y,S);T.transmission>0?r.unshift(z):T.transparent===!0?l.unshift(z):i.unshift(z)}function v(g,E,T){i.length>1&&i.sort(g||tR),r.length>1&&r.sort(E||k_),l.length>1&&l.sort(E||k_),T&&(i.reverse(),r.reverse(),l.reverse())}function x(){for(let g=e,E=s.length;g<E;g++){const T=s[g];if(T.id===null)break;T.id=null,T.object=null,T.geometry=null,T.material=null,T.group=null}}return{opaque:i,transmissive:r,transparent:l,init:u,push:m,unshift:p,finish:x,sort:v}}function nR(){let s=new WeakMap;function e(r,l){const u=s.get(r);let d;return u===void 0?(d=new X_,s.set(r,[d])):l>=u.length?(d=new X_,u.push(d)):d=u[l],d}function i(){s=new WeakMap}return{get:e,dispose:i}}function iR(){const s={};return{get:function(e){if(s[e.id]!==void 0)return s[e.id];let i;switch(e.type){case"DirectionalLight":i={direction:new ae,color:new Et};break;case"SpotLight":i={position:new ae,direction:new ae,color:new Et,distance:0,coneCos:0,penumbraCos:0,decay:0};break;case"PointLight":i={position:new ae,color:new Et,distance:0,decay:0};break;case"HemisphereLight":i={direction:new ae,skyColor:new Et,groundColor:new Et};break;case"RectAreaLight":i={color:new Et,position:new ae,halfWidth:new ae,halfHeight:new ae};break}return s[e.id]=i,i}}}function aR(){const s={};return{get:function(e){if(s[e.id]!==void 0)return s[e.id];let i;switch(e.type){case"DirectionalLight":i={shadowIntensity:1,shadowBias:0,shadowNormalBias:0,shadowRadius:1,shadowMapSize:new Ut};break;case"SpotLight":i={shadowIntensity:1,shadowBias:0,shadowNormalBias:0,shadowRadius:1,shadowMapSize:new Ut};break;case"PointLight":i={shadowIntensity:1,shadowBias:0,shadowNormalBias:0,shadowRadius:1,shadowMapSize:new Ut,shadowCameraNear:1,shadowCameraFar:1e3};break}return s[e.id]=i,i}}}let sR=0;function rR(s,e){return(e.castShadow?2:0)-(s.castShadow?2:0)+(e.map?1:0)-(s.map?1:0)}function oR(s){const e=new iR,i=aR(),r={version:0,hash:{directionalLength:-1,pointLength:-1,spotLength:-1,rectAreaLength:-1,hemiLength:-1,numDirectionalShadows:-1,numPointShadows:-1,numSpotShadows:-1,numSpotMaps:-1,numLightProbes:-1},ambient:[0,0,0],probe:[],directional:[],directionalShadow:[],directionalShadowMap:[],directionalShadowMatrix:[],spot:[],spotLightMap:[],spotShadow:[],spotShadowMap:[],spotLightMatrix:[],rectArea:[],rectAreaLTC1:null,rectAreaLTC2:null,point:[],pointShadow:[],pointShadowMap:[],pointShadowMatrix:[],hemi:[],numSpotLightShadowsWithMaps:0,numLightProbes:0};for(let p=0;p<9;p++)r.probe.push(new ae);const l=new ae,u=new fn,d=new fn;function h(p){let v=0,x=0,g=0;for(let L=0;L<9;L++)r.probe[L].set(0,0,0);let E=0,T=0,D=0,y=0,S=0,z=0,F=0,C=0,O=0,U=0,B=0;p.sort(rR);for(let L=0,W=p.length;L<W;L++){const V=p[L],Z=V.color,de=V.intensity,he=V.distance;let K=null;if(V.shadow&&V.shadow.map&&(V.shadow.map.texture.format===Hs?K=V.shadow.map.texture:K=V.shadow.map.depthTexture||V.shadow.map.texture),V.isAmbientLight)v+=Z.r*de,x+=Z.g*de,g+=Z.b*de;else if(V.isLightProbe){for(let N=0;N<9;N++)r.probe[N].addScaledVector(V.sh.coefficients[N],de);B++}else if(V.isDirectionalLight){const N=e.get(V);if(N.color.copy(V.color).multiplyScalar(V.intensity),V.castShadow){const I=V.shadow,$=i.get(V);$.shadowIntensity=I.intensity,$.shadowBias=I.bias,$.shadowNormalBias=I.normalBias,$.shadowRadius=I.radius,$.shadowMapSize=I.mapSize,r.directionalShadow[E]=$,r.directionalShadowMap[E]=K,r.directionalShadowMatrix[E]=V.shadow.matrix,z++}r.directional[E]=N,E++}else if(V.isSpotLight){const N=e.get(V);N.position.setFromMatrixPosition(V.matrixWorld),N.color.copy(Z).multiplyScalar(de),N.distance=he,N.coneCos=Math.cos(V.angle),N.penumbraCos=Math.cos(V.angle*(1-V.penumbra)),N.decay=V.decay,r.spot[D]=N;const I=V.shadow;if(V.map&&(r.spotLightMap[O]=V.map,O++,I.updateMatrices(V),V.castShadow&&U++),r.spotLightMatrix[D]=I.matrix,V.castShadow){const $=i.get(V);$.shadowIntensity=I.intensity,$.shadowBias=I.bias,$.shadowNormalBias=I.normalBias,$.shadowRadius=I.radius,$.shadowMapSize=I.mapSize,r.spotShadow[D]=$,r.spotShadowMap[D]=K,C++}D++}else if(V.isRectAreaLight){const N=e.get(V);N.color.copy(Z).multiplyScalar(de),N.halfWidth.set(V.width*.5,0,0),N.halfHeight.set(0,V.height*.5,0),r.rectArea[y]=N,y++}else if(V.isPointLight){const N=e.get(V);if(N.color.copy(V.color).multiplyScalar(V.intensity),N.distance=V.distance,N.decay=V.decay,V.castShadow){const I=V.shadow,$=i.get(V);$.shadowIntensity=I.intensity,$.shadowBias=I.bias,$.shadowNormalBias=I.normalBias,$.shadowRadius=I.radius,$.shadowMapSize=I.mapSize,$.shadowCameraNear=I.camera.near,$.shadowCameraFar=I.camera.far,r.pointShadow[T]=$,r.pointShadowMap[T]=K,r.pointShadowMatrix[T]=V.shadow.matrix,F++}r.point[T]=N,T++}else if(V.isHemisphereLight){const N=e.get(V);N.skyColor.copy(V.color).multiplyScalar(de),N.groundColor.copy(V.groundColor).multiplyScalar(de),r.hemi[S]=N,S++}}y>0&&(s.has("OES_texture_float_linear")===!0?(r.rectAreaLTC1=Ne.LTC_FLOAT_1,r.rectAreaLTC2=Ne.LTC_FLOAT_2):(r.rectAreaLTC1=Ne.LTC_HALF_1,r.rectAreaLTC2=Ne.LTC_HALF_2)),r.ambient[0]=v,r.ambient[1]=x,r.ambient[2]=g;const b=r.hash;(b.directionalLength!==E||b.pointLength!==T||b.spotLength!==D||b.rectAreaLength!==y||b.hemiLength!==S||b.numDirectionalShadows!==z||b.numPointShadows!==F||b.numSpotShadows!==C||b.numSpotMaps!==O||b.numLightProbes!==B)&&(r.directional.length=E,r.spot.length=D,r.rectArea.length=y,r.point.length=T,r.hemi.length=S,r.directionalShadow.length=z,r.directionalShadowMap.length=z,r.pointShadow.length=F,r.pointShadowMap.length=F,r.spotShadow.length=C,r.spotShadowMap.length=C,r.directionalShadowMatrix.length=z,r.pointShadowMatrix.length=F,r.spotLightMatrix.length=C+O-U,r.spotLightMap.length=O,r.numSpotLightShadowsWithMaps=U,r.numLightProbes=B,b.directionalLength=E,b.pointLength=T,b.spotLength=D,b.rectAreaLength=y,b.hemiLength=S,b.numDirectionalShadows=z,b.numPointShadows=F,b.numSpotShadows=C,b.numSpotMaps=O,b.numLightProbes=B,r.version=sR++)}function m(p,v){let x=0,g=0,E=0,T=0,D=0;const y=v.matrixWorldInverse;for(let S=0,z=p.length;S<z;S++){const F=p[S];if(F.isDirectionalLight){const C=r.directional[x];C.direction.setFromMatrixPosition(F.matrixWorld),l.setFromMatrixPosition(F.target.matrixWorld),C.direction.sub(l),C.direction.transformDirection(y),x++}else if(F.isSpotLight){const C=r.spot[E];C.position.setFromMatrixPosition(F.matrixWorld),C.position.applyMatrix4(y),C.direction.setFromMatrixPosition(F.matrixWorld),l.setFromMatrixPosition(F.target.matrixWorld),C.direction.sub(l),C.direction.transformDirection(y),E++}else if(F.isRectAreaLight){const C=r.rectArea[T];C.position.setFromMatrixPosition(F.matrixWorld),C.position.applyMatrix4(y),d.identity(),u.copy(F.matrixWorld),u.premultiply(y),d.extractRotation(u),C.halfWidth.set(F.width*.5,0,0),C.halfHeight.set(0,F.height*.5,0),C.halfWidth.applyMatrix4(d),C.halfHeight.applyMatrix4(d),T++}else if(F.isPointLight){const C=r.point[g];C.position.setFromMatrixPosition(F.matrixWorld),C.position.applyMatrix4(y),g++}else if(F.isHemisphereLight){const C=r.hemi[D];C.direction.setFromMatrixPosition(F.matrixWorld),C.direction.transformDirection(y),D++}}}return{setup:h,setupView:m,state:r}}function W_(s){const e=new oR(s),i=[],r=[],l=[];function u(g){x.camera=g,i.length=0,r.length=0,l.length=0}function d(g){i.push(g)}function h(g){r.push(g)}function m(g){l.push(g)}function p(){e.setup(i)}function v(g){e.setupView(i,g)}const x={lightsArray:i,shadowsArray:r,lightProbeGridArray:l,camera:null,lights:e,transmissionRenderTarget:{},textureUnits:0};return{init:u,state:x,setupLights:p,setupLightsView:v,pushLight:d,pushShadow:h,pushLightProbeGrid:m}}function lR(s){let e=new WeakMap;function i(l,u=0){const d=e.get(l);let h;return d===void 0?(h=new W_(s),e.set(l,[h])):u>=d.length?(h=new W_(s),d.push(h)):h=d[u],h}function r(){e=new WeakMap}return{get:i,dispose:r}}const cR=`void main() {
	gl_Position = vec4( position, 1.0 );
}`,uR=`uniform sampler2D shadow_pass;
uniform vec2 resolution;
uniform float radius;
void main() {
	const float samples = float( VSM_SAMPLES );
	float mean = 0.0;
	float squared_mean = 0.0;
	float uvStride = samples <= 1.0 ? 0.0 : 2.0 / ( samples - 1.0 );
	float uvStart = samples <= 1.0 ? 0.0 : - 1.0;
	for ( float i = 0.0; i < samples; i ++ ) {
		float uvOffset = uvStart + i * uvStride;
		#ifdef HORIZONTAL_PASS
			vec2 distribution = texture2D( shadow_pass, ( gl_FragCoord.xy + vec2( uvOffset, 0.0 ) * radius ) / resolution ).rg;
			mean += distribution.x;
			squared_mean += distribution.y * distribution.y + distribution.x * distribution.x;
		#else
			float depth = texture2D( shadow_pass, ( gl_FragCoord.xy + vec2( 0.0, uvOffset ) * radius ) / resolution ).r;
			mean += depth;
			squared_mean += depth * depth;
		#endif
	}
	mean = mean / samples;
	squared_mean = squared_mean / samples;
	float std_dev = sqrt( max( 0.0, squared_mean - mean * mean ) );
	gl_FragColor = vec4( mean, std_dev, 0.0, 1.0 );
}`,fR=[new ae(1,0,0),new ae(-1,0,0),new ae(0,1,0),new ae(0,-1,0),new ae(0,0,1),new ae(0,0,-1)],dR=[new ae(0,-1,0),new ae(0,-1,0),new ae(0,0,1),new ae(0,0,-1),new ae(0,-1,0),new ae(0,-1,0)],q_=new fn,qo=new ae,Vd=new ae;function hR(s,e,i){let r=new Tv;const l=new Ut,u=new Ut,d=new on,h=new AE,m=new RE,p={},v=i.maxTextureSize,x={[ls]:Yn,[Yn]:ls,[xa]:xa},g=new Yi({defines:{VSM_SAMPLES:8},uniforms:{shadow_pass:{value:null},resolution:{value:new Ut},radius:{value:4}},vertexShader:cR,fragmentShader:uR}),E=g.clone();E.defines.HORIZONTAL_PASS=1;const T=new Li;T.setAttribute("position",new Si(new Float32Array([-1,-1,.5,3,-1,.5,-1,3,.5]),3));const D=new Ui(T,g),y=this;this.enabled=!1,this.autoUpdate=!0,this.needsUpdate=!1,this.type=Gc;let S=this.type;this.render=function(U,B,b){if(y.enabled===!1||y.autoUpdate===!1&&y.needsUpdate===!1||U.length===0)return;this.type===ey&&(et("WebGLShadowMap: PCFSoftShadowMap has been deprecated. Using PCFShadowMap instead."),this.type=Gc);const L=s.getRenderTarget(),W=s.getActiveCubeFace(),V=s.getActiveMipmapLevel(),Z=s.state;Z.setBlending(Ma),Z.buffers.depth.getReversed()===!0?Z.buffers.color.setClear(0,0,0,0):Z.buffers.color.setClear(1,1,1,1),Z.buffers.depth.setTest(!0),Z.setScissorTest(!1);const de=S!==this.type;de&&B.traverse(function(he){he.material&&(Array.isArray(he.material)?he.material.forEach(K=>K.needsUpdate=!0):he.material.needsUpdate=!0)});for(let he=0,K=U.length;he<K;he++){const N=U[he],I=N.shadow;if(I===void 0){et("WebGLShadowMap:",N,"has no shadow.");continue}if(I.autoUpdate===!1&&I.needsUpdate===!1)continue;l.copy(I.mapSize);const $=I.getFrameExtents();l.multiply($),u.copy(I.mapSize),(l.x>v||l.y>v)&&(l.x>v&&(u.x=Math.floor(v/$.x),l.x=u.x*$.x,I.mapSize.x=u.x),l.y>v&&(u.y=Math.floor(v/$.y),l.y=u.y*$.y,I.mapSize.y=u.y));const se=s.state.buffers.depth.getReversed();if(I.camera._reversedDepth=se,I.map===null||de===!0){if(I.map!==null&&(I.map.depthTexture!==null&&(I.map.depthTexture.dispose(),I.map.depthTexture=null),I.map.dispose()),this.type===Yo){if(N.isPointLight){et("WebGLShadowMap: VSM shadow maps are not supported for PointLights. Use PCF or BasicShadowMap instead.");continue}I.map=new Wi(l.x,l.y,{format:Hs,type:ba,minFilter:On,magFilter:On,generateMipmaps:!1}),I.map.texture.name=N.name+".shadowMap",I.map.depthTexture=new Vr(l.x,l.y,Vi),I.map.depthTexture.name=N.name+".shadowMapDepth",I.map.depthTexture.format=Ta,I.map.depthTexture.compareFunction=null,I.map.depthTexture.minFilter=Dn,I.map.depthTexture.magFilter=Dn}else N.isPointLight?(I.map=new Ov(l.x),I.map.depthTexture=new SE(l.x,qi)):(I.map=new Wi(l.x,l.y),I.map.depthTexture=new Vr(l.x,l.y,qi)),I.map.depthTexture.name=N.name+".shadowMap",I.map.depthTexture.format=Ta,this.type===Gc?(I.map.depthTexture.compareFunction=se?qh:Wh,I.map.depthTexture.minFilter=On,I.map.depthTexture.magFilter=On):(I.map.depthTexture.compareFunction=null,I.map.depthTexture.minFilter=Dn,I.map.depthTexture.magFilter=Dn);I.camera.updateProjectionMatrix()}const pe=I.map.isWebGLCubeRenderTarget?6:1;for(let xe=0;xe<pe;xe++){if(I.map.isWebGLCubeRenderTarget)s.setRenderTarget(I.map,xe),s.clear();else{xe===0&&(s.setRenderTarget(I.map),s.clear());const Se=I.getViewport(xe);d.set(u.x*Se.x,u.y*Se.y,u.x*Se.z,u.y*Se.w),Z.viewport(d)}if(N.isPointLight){const Se=I.camera,Te=I.matrix,tt=N.distance||Se.far;tt!==Se.far&&(Se.far=tt,Se.updateProjectionMatrix()),qo.setFromMatrixPosition(N.matrixWorld),Se.position.copy(qo),Vd.copy(Se.position),Vd.add(fR[xe]),Se.up.copy(dR[xe]),Se.lookAt(Vd),Se.updateMatrixWorld(),Te.makeTranslation(-qo.x,-qo.y,-qo.z),q_.multiplyMatrices(Se.projectionMatrix,Se.matrixWorldInverse),I._frustum.setFromProjectionMatrix(q_,Se.coordinateSystem,Se.reversedDepth)}else I.updateMatrices(N);r=I.getFrustum(),C(B,b,I.camera,N,this.type)}I.isPointLightShadow!==!0&&this.type===Yo&&z(I,b),I.needsUpdate=!1}S=this.type,y.needsUpdate=!1,s.setRenderTarget(L,W,V)};function z(U,B){const b=e.update(D);g.defines.VSM_SAMPLES!==U.blurSamples&&(g.defines.VSM_SAMPLES=U.blurSamples,E.defines.VSM_SAMPLES=U.blurSamples,g.needsUpdate=!0,E.needsUpdate=!0),U.mapPass===null&&(U.mapPass=new Wi(l.x,l.y,{format:Hs,type:ba})),g.uniforms.shadow_pass.value=U.map.depthTexture,g.uniforms.resolution.value=U.mapSize,g.uniforms.radius.value=U.radius,s.setRenderTarget(U.mapPass),s.clear(),s.renderBufferDirect(B,null,b,g,D,null),E.uniforms.shadow_pass.value=U.mapPass.texture,E.uniforms.resolution.value=U.mapSize,E.uniforms.radius.value=U.radius,s.setRenderTarget(U.map),s.clear(),s.renderBufferDirect(B,null,b,E,D,null)}function F(U,B,b,L){let W=null;const V=b.isPointLight===!0?U.customDistanceMaterial:U.customDepthMaterial;if(V!==void 0)W=V;else if(W=b.isPointLight===!0?m:h,s.localClippingEnabled&&B.clipShadows===!0&&Array.isArray(B.clippingPlanes)&&B.clippingPlanes.length!==0||B.displacementMap&&B.displacementScale!==0||B.alphaMap&&B.alphaTest>0||B.map&&B.alphaTest>0||B.alphaToCoverage===!0){const Z=W.uuid,de=B.uuid;let he=p[Z];he===void 0&&(he={},p[Z]=he);let K=he[de];K===void 0&&(K=W.clone(),he[de]=K,B.addEventListener("dispose",O)),W=K}if(W.visible=B.visible,W.wireframe=B.wireframe,L===Yo?W.side=B.shadowSide!==null?B.shadowSide:B.side:W.side=B.shadowSide!==null?B.shadowSide:x[B.side],W.alphaMap=B.alphaMap,W.alphaTest=B.alphaToCoverage===!0?.5:B.alphaTest,W.map=B.map,W.clipShadows=B.clipShadows,W.clippingPlanes=B.clippingPlanes,W.clipIntersection=B.clipIntersection,W.displacementMap=B.displacementMap,W.displacementScale=B.displacementScale,W.displacementBias=B.displacementBias,W.wireframeLinewidth=B.wireframeLinewidth,W.linewidth=B.linewidth,b.isPointLight===!0&&W.isMeshDistanceMaterial===!0){const Z=s.properties.get(W);Z.light=b}return W}function C(U,B,b,L,W){if(U.visible===!1)return;if(U.layers.test(B.layers)&&(U.isMesh||U.isLine||U.isPoints)&&(U.castShadow||U.receiveShadow&&W===Yo)&&(!U.frustumCulled||r.intersectsObject(U))){U.modelViewMatrix.multiplyMatrices(b.matrixWorldInverse,U.matrixWorld);const de=e.update(U),he=U.material;if(Array.isArray(he)){const K=de.groups;for(let N=0,I=K.length;N<I;N++){const $=K[N],se=he[$.materialIndex];if(se&&se.visible){const pe=F(U,se,L,W);U.onBeforeShadow(s,U,B,b,de,pe,$),s.renderBufferDirect(b,null,de,pe,U,$),U.onAfterShadow(s,U,B,b,de,pe,$)}}}else if(he.visible){const K=F(U,he,L,W);U.onBeforeShadow(s,U,B,b,de,K,null),s.renderBufferDirect(b,null,de,K,U,null),U.onAfterShadow(s,U,B,b,de,K,null)}}const Z=U.children;for(let de=0,he=Z.length;de<he;de++)C(Z[de],B,b,L,W)}function O(U){U.target.removeEventListener("dispose",O);for(const b in p){const L=p[b],W=U.target.uuid;W in L&&(L[W].dispose(),delete L[W])}}}function pR(s,e){function i(){let G=!1;const Ee=new on;let me=null;const Re=new on(0,0,0,0);return{setMask:function(Oe){me!==Oe&&!G&&(s.colorMask(Oe,Oe,Oe,Oe),me=Oe)},setLocked:function(Oe){G=Oe},setClear:function(Oe,ve,ke,Ie,Qt){Qt===!0&&(Oe*=Ie,ve*=Ie,ke*=Ie),Ee.set(Oe,ve,ke,Ie),Re.equals(Ee)===!1&&(s.clearColor(Oe,ve,ke,Ie),Re.copy(Ee))},reset:function(){G=!1,me=null,Re.set(-1,0,0,0)}}}function r(){let G=!1,Ee=!1,me=null,Re=null,Oe=null;return{setReversed:function(ve){if(Ee!==ve){const ke=e.get("EXT_clip_control");ve?ke.clipControlEXT(ke.LOWER_LEFT_EXT,ke.ZERO_TO_ONE_EXT):ke.clipControlEXT(ke.LOWER_LEFT_EXT,ke.NEGATIVE_ONE_TO_ONE_EXT),Ee=ve;const Ie=Oe;Oe=null,this.setClear(Ie)}},getReversed:function(){return Ee},setTest:function(ve){ve?Me(s.DEPTH_TEST):$e(s.DEPTH_TEST)},setMask:function(ve){me!==ve&&!G&&(s.depthMask(ve),me=ve)},setFunc:function(ve){if(Ee&&(ve=Ny[ve]),Re!==ve){switch(ve){case Qd:s.depthFunc(s.NEVER);break;case Jd:s.depthFunc(s.ALWAYS);break;case $d:s.depthFunc(s.LESS);break;case Hr:s.depthFunc(s.LEQUAL);break;case jd:s.depthFunc(s.EQUAL);break;case eh:s.depthFunc(s.GEQUAL);break;case th:s.depthFunc(s.GREATER);break;case nh:s.depthFunc(s.NOTEQUAL);break;default:s.depthFunc(s.LEQUAL)}Re=ve}},setLocked:function(ve){G=ve},setClear:function(ve){Oe!==ve&&(Oe=ve,Ee&&(ve=1-ve),s.clearDepth(ve))},reset:function(){G=!1,me=null,Re=null,Oe=null,Ee=!1}}}function l(){let G=!1,Ee=null,me=null,Re=null,Oe=null,ve=null,ke=null,Ie=null,Qt=null;return{setTest:function(Nt){G||(Nt?Me(s.STENCIL_TEST):$e(s.STENCIL_TEST))},setMask:function(Nt){Ee!==Nt&&!G&&(s.stencilMask(Nt),Ee=Nt)},setFunc:function(Nt,Kn,Qn){(me!==Nt||Re!==Kn||Oe!==Qn)&&(s.stencilFunc(Nt,Kn,Qn),me=Nt,Re=Kn,Oe=Qn)},setOp:function(Nt,Kn,Qn){(ve!==Nt||ke!==Kn||Ie!==Qn)&&(s.stencilOp(Nt,Kn,Qn),ve=Nt,ke=Kn,Ie=Qn)},setLocked:function(Nt){G=Nt},setClear:function(Nt){Qt!==Nt&&(s.clearStencil(Nt),Qt=Nt)},reset:function(){G=!1,Ee=null,me=null,Re=null,Oe=null,ve=null,ke=null,Ie=null,Qt=null}}}const u=new i,d=new r,h=new l,m=new WeakMap,p=new WeakMap;let v={},x={},g={},E=new WeakMap,T=[],D=null,y=!1,S=null,z=null,F=null,C=null,O=null,U=null,B=null,b=new Et(0,0,0),L=0,W=!1,V=null,Z=null,de=null,he=null,K=null;const N=s.getParameter(s.MAX_COMBINED_TEXTURE_IMAGE_UNITS);let I=!1,$=0;const se=s.getParameter(s.VERSION);se.indexOf("WebGL")!==-1?($=parseFloat(/^WebGL (\d)/.exec(se)[1]),I=$>=1):se.indexOf("OpenGL ES")!==-1&&($=parseFloat(/^OpenGL ES (\d)/.exec(se)[1]),I=$>=2);let pe=null,xe={};const Se=s.getParameter(s.SCISSOR_BOX),Te=s.getParameter(s.VIEWPORT),tt=new on().fromArray(Se),nt=new on().fromArray(Te);function ue(G,Ee,me,Re){const Oe=new Uint8Array(4),ve=s.createTexture();s.bindTexture(G,ve),s.texParameteri(G,s.TEXTURE_MIN_FILTER,s.NEAREST),s.texParameteri(G,s.TEXTURE_MAG_FILTER,s.NEAREST);for(let ke=0;ke<me;ke++)G===s.TEXTURE_3D||G===s.TEXTURE_2D_ARRAY?s.texImage3D(Ee,0,s.RGBA,1,1,Re,0,s.RGBA,s.UNSIGNED_BYTE,Oe):s.texImage2D(Ee+ke,0,s.RGBA,1,1,0,s.RGBA,s.UNSIGNED_BYTE,Oe);return ve}const Ae={};Ae[s.TEXTURE_2D]=ue(s.TEXTURE_2D,s.TEXTURE_2D,1),Ae[s.TEXTURE_CUBE_MAP]=ue(s.TEXTURE_CUBE_MAP,s.TEXTURE_CUBE_MAP_POSITIVE_X,6),Ae[s.TEXTURE_2D_ARRAY]=ue(s.TEXTURE_2D_ARRAY,s.TEXTURE_2D_ARRAY,1,1),Ae[s.TEXTURE_3D]=ue(s.TEXTURE_3D,s.TEXTURE_3D,1,1),u.setClear(0,0,0,1),d.setClear(1),h.setClear(0),Me(s.DEPTH_TEST),d.setFunc(Hr),en(!1),tn(q0),Me(s.CULL_FACE),St(Ma);function Me(G){v[G]!==!0&&(s.enable(G),v[G]=!0)}function $e(G){v[G]!==!1&&(s.disable(G),v[G]=!1)}function at(G,Ee){return g[G]!==Ee?(s.bindFramebuffer(G,Ee),g[G]=Ee,G===s.DRAW_FRAMEBUFFER&&(g[s.FRAMEBUFFER]=Ee),G===s.FRAMEBUFFER&&(g[s.DRAW_FRAMEBUFFER]=Ee),!0):!1}function Ze(G,Ee){let me=T,Re=!1;if(G){me=E.get(Ee),me===void 0&&(me=[],E.set(Ee,me));const Oe=G.textures;if(me.length!==Oe.length||me[0]!==s.COLOR_ATTACHMENT0){for(let ve=0,ke=Oe.length;ve<ke;ve++)me[ve]=s.COLOR_ATTACHMENT0+ve;me.length=Oe.length,Re=!0}}else me[0]!==s.BACK&&(me[0]=s.BACK,Re=!0);Re&&s.drawBuffers(me)}function Yt(G){return D!==G?(s.useProgram(G),D=G,!0):!1}const ut={[Ps]:s.FUNC_ADD,[ny]:s.FUNC_SUBTRACT,[iy]:s.FUNC_REVERSE_SUBTRACT};ut[ay]=s.MIN,ut[sy]=s.MAX;const xt={[ry]:s.ZERO,[oy]:s.ONE,[ly]:s.SRC_COLOR,[Zd]:s.SRC_ALPHA,[py]:s.SRC_ALPHA_SATURATE,[dy]:s.DST_COLOR,[uy]:s.DST_ALPHA,[cy]:s.ONE_MINUS_SRC_COLOR,[Kd]:s.ONE_MINUS_SRC_ALPHA,[hy]:s.ONE_MINUS_DST_COLOR,[fy]:s.ONE_MINUS_DST_ALPHA,[my]:s.CONSTANT_COLOR,[gy]:s.ONE_MINUS_CONSTANT_COLOR,[_y]:s.CONSTANT_ALPHA,[vy]:s.ONE_MINUS_CONSTANT_ALPHA};function St(G,Ee,me,Re,Oe,ve,ke,Ie,Qt,Nt){if(G===Ma){y===!0&&($e(s.BLEND),y=!1);return}if(y===!1&&(Me(s.BLEND),y=!0),G!==ty){if(G!==S||Nt!==W){if((z!==Ps||O!==Ps)&&(s.blendEquation(s.FUNC_ADD),z=Ps,O=Ps),Nt)switch(G){case Fr:s.blendFuncSeparate(s.ONE,s.ONE_MINUS_SRC_ALPHA,s.ONE,s.ONE_MINUS_SRC_ALPHA);break;case Yd:s.blendFunc(s.ONE,s.ONE);break;case Y0:s.blendFuncSeparate(s.ZERO,s.ONE_MINUS_SRC_COLOR,s.ZERO,s.ONE);break;case Z0:s.blendFuncSeparate(s.DST_COLOR,s.ONE_MINUS_SRC_ALPHA,s.ZERO,s.ONE);break;default:bt("WebGLState: Invalid blending: ",G);break}else switch(G){case Fr:s.blendFuncSeparate(s.SRC_ALPHA,s.ONE_MINUS_SRC_ALPHA,s.ONE,s.ONE_MINUS_SRC_ALPHA);break;case Yd:s.blendFuncSeparate(s.SRC_ALPHA,s.ONE,s.ONE,s.ONE);break;case Y0:bt("WebGLState: SubtractiveBlending requires material.premultipliedAlpha = true");break;case Z0:bt("WebGLState: MultiplyBlending requires material.premultipliedAlpha = true");break;default:bt("WebGLState: Invalid blending: ",G);break}F=null,C=null,U=null,B=null,b.set(0,0,0),L=0,S=G,W=Nt}return}Oe=Oe||Ee,ve=ve||me,ke=ke||Re,(Ee!==z||Oe!==O)&&(s.blendEquationSeparate(ut[Ee],ut[Oe]),z=Ee,O=Oe),(me!==F||Re!==C||ve!==U||ke!==B)&&(s.blendFuncSeparate(xt[me],xt[Re],xt[ve],xt[ke]),F=me,C=Re,U=ve,B=ke),(Ie.equals(b)===!1||Qt!==L)&&(s.blendColor(Ie.r,Ie.g,Ie.b,Qt),b.copy(Ie),L=Qt),S=G,W=!1}function ft(G,Ee){G.side===xa?$e(s.CULL_FACE):Me(s.CULL_FACE);let me=G.side===Yn;Ee&&(me=!me),en(me),G.blending===Fr&&G.transparent===!1?St(Ma):St(G.blending,G.blendEquation,G.blendSrc,G.blendDst,G.blendEquationAlpha,G.blendSrcAlpha,G.blendDstAlpha,G.blendColor,G.blendAlpha,G.premultipliedAlpha),d.setFunc(G.depthFunc),d.setTest(G.depthTest),d.setMask(G.depthWrite),u.setMask(G.colorWrite);const Re=G.stencilWrite;h.setTest(Re),Re&&(h.setMask(G.stencilWriteMask),h.setFunc(G.stencilFunc,G.stencilRef,G.stencilFuncMask),h.setOp(G.stencilFail,G.stencilZFail,G.stencilZPass)),ln(G.polygonOffset,G.polygonOffsetFactor,G.polygonOffsetUnits),G.alphaToCoverage===!0?Me(s.SAMPLE_ALPHA_TO_COVERAGE):$e(s.SAMPLE_ALPHA_TO_COVERAGE)}function en(G){V!==G&&(G?s.frontFace(s.CW):s.frontFace(s.CCW),V=G)}function tn(G){G!==$M?(Me(s.CULL_FACE),G!==Z&&(G===q0?s.cullFace(s.BACK):G===jM?s.cullFace(s.FRONT):s.cullFace(s.FRONT_AND_BACK))):$e(s.CULL_FACE),Z=G}function nn(G){G!==de&&(I&&s.lineWidth(G),de=G)}function ln(G,Ee,me){G?(Me(s.POLYGON_OFFSET_FILL),(he!==Ee||K!==me)&&(he=Ee,K=me,d.getReversed()&&(Ee=-Ee),s.polygonOffset(Ee,me))):$e(s.POLYGON_OFFSET_FILL)}function qt(G){G?Me(s.SCISSOR_TEST):$e(s.SCISSOR_TEST)}function an(G){G===void 0&&(G=s.TEXTURE0+N-1),pe!==G&&(s.activeTexture(G),pe=G)}function X(G,Ee,me){me===void 0&&(pe===null?me=s.TEXTURE0+N-1:me=pe);let Re=xe[me];Re===void 0&&(Re={type:void 0,texture:void 0},xe[me]=Re),(Re.type!==G||Re.texture!==Ee)&&(pe!==me&&(s.activeTexture(me),pe=me),s.bindTexture(G,Ee||Ae[G]),Re.type=G,Re.texture=Ee)}function Ht(){const G=xe[pe];G!==void 0&&G.type!==void 0&&(s.bindTexture(G.type,null),G.type=void 0,G.texture=void 0)}function wt(){try{s.compressedTexImage2D(...arguments)}catch(G){bt("WebGLState:",G)}}function w(){try{s.compressedTexImage3D(...arguments)}catch(G){bt("WebGLState:",G)}}function M(){try{s.texSubImage2D(...arguments)}catch(G){bt("WebGLState:",G)}}function Y(){try{s.texSubImage3D(...arguments)}catch(G){bt("WebGLState:",G)}}function te(){try{s.compressedTexSubImage2D(...arguments)}catch(G){bt("WebGLState:",G)}}function le(){try{s.compressedTexSubImage3D(...arguments)}catch(G){bt("WebGLState:",G)}}function ye(){try{s.texStorage2D(...arguments)}catch(G){bt("WebGLState:",G)}}function Ce(){try{s.texStorage3D(...arguments)}catch(G){bt("WebGLState:",G)}}function oe(){try{s.texImage2D(...arguments)}catch(G){bt("WebGLState:",G)}}function ce(){try{s.texImage3D(...arguments)}catch(G){bt("WebGLState:",G)}}function be(G){return x[G]!==void 0?x[G]:s.getParameter(G)}function Pe(G,Ee){x[G]!==Ee&&(s.pixelStorei(G,Ee),x[G]=Ee)}function Ue(G){tt.equals(G)===!1&&(s.scissor(G.x,G.y,G.z,G.w),tt.copy(G))}function we(G){nt.equals(G)===!1&&(s.viewport(G.x,G.y,G.z,G.w),nt.copy(G))}function Ye(G,Ee){let me=p.get(Ee);me===void 0&&(me=new WeakMap,p.set(Ee,me));let Re=me.get(G);Re===void 0&&(Re=s.getUniformBlockIndex(Ee,G.name),me.set(G,Re))}function Ke(G,Ee){const Re=p.get(Ee).get(G);m.get(Ee)!==Re&&(s.uniformBlockBinding(Ee,Re,G.__bindingPointIndex),m.set(Ee,Re))}function it(){s.disable(s.BLEND),s.disable(s.CULL_FACE),s.disable(s.DEPTH_TEST),s.disable(s.POLYGON_OFFSET_FILL),s.disable(s.SCISSOR_TEST),s.disable(s.STENCIL_TEST),s.disable(s.SAMPLE_ALPHA_TO_COVERAGE),s.blendEquation(s.FUNC_ADD),s.blendFunc(s.ONE,s.ZERO),s.blendFuncSeparate(s.ONE,s.ZERO,s.ONE,s.ZERO),s.blendColor(0,0,0,0),s.colorMask(!0,!0,!0,!0),s.clearColor(0,0,0,0),s.depthMask(!0),s.depthFunc(s.LESS),d.setReversed(!1),s.clearDepth(1),s.stencilMask(4294967295),s.stencilFunc(s.ALWAYS,0,4294967295),s.stencilOp(s.KEEP,s.KEEP,s.KEEP),s.clearStencil(0),s.cullFace(s.BACK),s.frontFace(s.CCW),s.polygonOffset(0,0),s.activeTexture(s.TEXTURE0),s.bindFramebuffer(s.FRAMEBUFFER,null),s.bindFramebuffer(s.DRAW_FRAMEBUFFER,null),s.bindFramebuffer(s.READ_FRAMEBUFFER,null),s.useProgram(null),s.lineWidth(1),s.scissor(0,0,s.canvas.width,s.canvas.height),s.viewport(0,0,s.canvas.width,s.canvas.height),s.pixelStorei(s.PACK_ALIGNMENT,4),s.pixelStorei(s.UNPACK_ALIGNMENT,4),s.pixelStorei(s.UNPACK_FLIP_Y_WEBGL,!1),s.pixelStorei(s.UNPACK_PREMULTIPLY_ALPHA_WEBGL,!1),s.pixelStorei(s.UNPACK_COLORSPACE_CONVERSION_WEBGL,s.BROWSER_DEFAULT_WEBGL),s.pixelStorei(s.PACK_ROW_LENGTH,0),s.pixelStorei(s.PACK_SKIP_PIXELS,0),s.pixelStorei(s.PACK_SKIP_ROWS,0),s.pixelStorei(s.UNPACK_ROW_LENGTH,0),s.pixelStorei(s.UNPACK_IMAGE_HEIGHT,0),s.pixelStorei(s.UNPACK_SKIP_PIXELS,0),s.pixelStorei(s.UNPACK_SKIP_ROWS,0),s.pixelStorei(s.UNPACK_SKIP_IMAGES,0),v={},x={},pe=null,xe={},g={},E=new WeakMap,T=[],D=null,y=!1,S=null,z=null,F=null,C=null,O=null,U=null,B=null,b=new Et(0,0,0),L=0,W=!1,V=null,Z=null,de=null,he=null,K=null,tt.set(0,0,s.canvas.width,s.canvas.height),nt.set(0,0,s.canvas.width,s.canvas.height),u.reset(),d.reset(),h.reset()}return{buffers:{color:u,depth:d,stencil:h},enable:Me,disable:$e,bindFramebuffer:at,drawBuffers:Ze,useProgram:Yt,setBlending:St,setMaterial:ft,setFlipSided:en,setCullFace:tn,setLineWidth:nn,setPolygonOffset:ln,setScissorTest:qt,activeTexture:an,bindTexture:X,unbindTexture:Ht,compressedTexImage2D:wt,compressedTexImage3D:w,texImage2D:oe,texImage3D:ce,pixelStorei:Pe,getParameter:be,updateUBOMapping:Ye,uniformBlockBinding:Ke,texStorage2D:ye,texStorage3D:Ce,texSubImage2D:M,texSubImage3D:Y,compressedTexSubImage2D:te,compressedTexSubImage3D:le,scissor:Ue,viewport:we,reset:it}}function mR(s,e,i,r,l,u,d){const h=e.has("WEBGL_multisampled_render_to_texture")?e.get("WEBGL_multisampled_render_to_texture"):null,m=typeof navigator>"u"?!1:/OculusBrowser/g.test(navigator.userAgent),p=new Ut,v=new WeakMap,x=new Set;let g;const E=new WeakMap;let T=!1;try{T=typeof OffscreenCanvas<"u"&&new OffscreenCanvas(1,1).getContext("2d")!==null}catch{}function D(w,M){return T?new OffscreenCanvas(w,M):$c("canvas")}function y(w,M,Y){let te=1;const le=wt(w);if((le.width>Y||le.height>Y)&&(te=Y/Math.max(le.width,le.height)),te<1)if(typeof HTMLImageElement<"u"&&w instanceof HTMLImageElement||typeof HTMLCanvasElement<"u"&&w instanceof HTMLCanvasElement||typeof ImageBitmap<"u"&&w instanceof ImageBitmap||typeof VideoFrame<"u"&&w instanceof VideoFrame){const ye=Math.floor(te*le.width),Ce=Math.floor(te*le.height);g===void 0&&(g=D(ye,Ce));const oe=M?D(ye,Ce):g;return oe.width=ye,oe.height=Ce,oe.getContext("2d").drawImage(w,0,0,ye,Ce),et("WebGLRenderer: Texture has been resized from ("+le.width+"x"+le.height+") to ("+ye+"x"+Ce+")."),oe}else return"data"in w&&et("WebGLRenderer: Image in DataTexture is too big ("+le.width+"x"+le.height+")."),w;return w}function S(w){return w.generateMipmaps}function z(w){s.generateMipmap(w)}function F(w){return w.isWebGLCubeRenderTarget?s.TEXTURE_CUBE_MAP:w.isWebGL3DRenderTarget?s.TEXTURE_3D:w.isWebGLArrayRenderTarget||w.isCompressedArrayTexture?s.TEXTURE_2D_ARRAY:s.TEXTURE_2D}function C(w,M,Y,te,le,ye=!1){if(w!==null){if(s[w]!==void 0)return s[w];et("WebGLRenderer: Attempt to use non-existing WebGL internal format '"+w+"'")}let Ce;te&&(Ce=e.get("EXT_texture_norm16"),Ce||et("WebGLRenderer: Unable to use normalized textures without EXT_texture_norm16 extension"));let oe=M;if(M===s.RED&&(Y===s.FLOAT&&(oe=s.R32F),Y===s.HALF_FLOAT&&(oe=s.R16F),Y===s.UNSIGNED_BYTE&&(oe=s.R8),Y===s.UNSIGNED_SHORT&&Ce&&(oe=Ce.R16_EXT),Y===s.SHORT&&Ce&&(oe=Ce.R16_SNORM_EXT)),M===s.RED_INTEGER&&(Y===s.UNSIGNED_BYTE&&(oe=s.R8UI),Y===s.UNSIGNED_SHORT&&(oe=s.R16UI),Y===s.UNSIGNED_INT&&(oe=s.R32UI),Y===s.BYTE&&(oe=s.R8I),Y===s.SHORT&&(oe=s.R16I),Y===s.INT&&(oe=s.R32I)),M===s.RG&&(Y===s.FLOAT&&(oe=s.RG32F),Y===s.HALF_FLOAT&&(oe=s.RG16F),Y===s.UNSIGNED_BYTE&&(oe=s.RG8),Y===s.UNSIGNED_SHORT&&Ce&&(oe=Ce.RG16_EXT),Y===s.SHORT&&Ce&&(oe=Ce.RG16_SNORM_EXT)),M===s.RG_INTEGER&&(Y===s.UNSIGNED_BYTE&&(oe=s.RG8UI),Y===s.UNSIGNED_SHORT&&(oe=s.RG16UI),Y===s.UNSIGNED_INT&&(oe=s.RG32UI),Y===s.BYTE&&(oe=s.RG8I),Y===s.SHORT&&(oe=s.RG16I),Y===s.INT&&(oe=s.RG32I)),M===s.RGB_INTEGER&&(Y===s.UNSIGNED_BYTE&&(oe=s.RGB8UI),Y===s.UNSIGNED_SHORT&&(oe=s.RGB16UI),Y===s.UNSIGNED_INT&&(oe=s.RGB32UI),Y===s.BYTE&&(oe=s.RGB8I),Y===s.SHORT&&(oe=s.RGB16I),Y===s.INT&&(oe=s.RGB32I)),M===s.RGBA_INTEGER&&(Y===s.UNSIGNED_BYTE&&(oe=s.RGBA8UI),Y===s.UNSIGNED_SHORT&&(oe=s.RGBA16UI),Y===s.UNSIGNED_INT&&(oe=s.RGBA32UI),Y===s.BYTE&&(oe=s.RGBA8I),Y===s.SHORT&&(oe=s.RGBA16I),Y===s.INT&&(oe=s.RGBA32I)),M===s.RGB&&(Y===s.UNSIGNED_SHORT&&Ce&&(oe=Ce.RGB16_EXT),Y===s.SHORT&&Ce&&(oe=Ce.RGB16_SNORM_EXT),Y===s.UNSIGNED_INT_5_9_9_9_REV&&(oe=s.RGB9_E5),Y===s.UNSIGNED_INT_10F_11F_11F_REV&&(oe=s.R11F_G11F_B10F)),M===s.RGBA){const ce=ye?Qc:yt.getTransfer(le);Y===s.FLOAT&&(oe=s.RGBA32F),Y===s.HALF_FLOAT&&(oe=s.RGBA16F),Y===s.UNSIGNED_BYTE&&(oe=ce===It?s.SRGB8_ALPHA8:s.RGBA8),Y===s.UNSIGNED_SHORT&&Ce&&(oe=Ce.RGBA16_EXT),Y===s.SHORT&&Ce&&(oe=Ce.RGBA16_SNORM_EXT),Y===s.UNSIGNED_SHORT_4_4_4_4&&(oe=s.RGBA4),Y===s.UNSIGNED_SHORT_5_5_5_1&&(oe=s.RGB5_A1)}return(oe===s.R16F||oe===s.R32F||oe===s.RG16F||oe===s.RG32F||oe===s.RGBA16F||oe===s.RGBA32F)&&e.get("EXT_color_buffer_float"),oe}function O(w,M){let Y;return w?M===null||M===qi||M===$o?Y=s.DEPTH24_STENCIL8:M===Vi?Y=s.DEPTH32F_STENCIL8:M===Jo&&(Y=s.DEPTH24_STENCIL8,et("DepthTexture: 16 bit depth attachment is not supported with stencil. Using 24-bit attachment.")):M===null||M===qi||M===$o?Y=s.DEPTH_COMPONENT24:M===Vi?Y=s.DEPTH_COMPONENT32F:M===Jo&&(Y=s.DEPTH_COMPONENT16),Y}function U(w,M){return S(w)===!0||w.isFramebufferTexture&&w.minFilter!==Dn&&w.minFilter!==On?Math.log2(Math.max(M.width,M.height))+1:w.mipmaps!==void 0&&w.mipmaps.length>0?w.mipmaps.length:w.isCompressedTexture&&Array.isArray(w.image)?M.mipmaps.length:1}function B(w){const M=w.target;M.removeEventListener("dispose",B),L(M),M.isVideoTexture&&v.delete(M),M.isHTMLTexture&&x.delete(M)}function b(w){const M=w.target;M.removeEventListener("dispose",b),V(M)}function L(w){const M=r.get(w);if(M.__webglInit===void 0)return;const Y=w.source,te=E.get(Y);if(te){const le=te[M.__cacheKey];le.usedTimes--,le.usedTimes===0&&W(w),Object.keys(te).length===0&&E.delete(Y)}r.remove(w)}function W(w){const M=r.get(w);s.deleteTexture(M.__webglTexture);const Y=w.source,te=E.get(Y);delete te[M.__cacheKey],d.memory.textures--}function V(w){const M=r.get(w);if(w.depthTexture&&(w.depthTexture.dispose(),r.remove(w.depthTexture)),w.isWebGLCubeRenderTarget)for(let te=0;te<6;te++){if(Array.isArray(M.__webglFramebuffer[te]))for(let le=0;le<M.__webglFramebuffer[te].length;le++)s.deleteFramebuffer(M.__webglFramebuffer[te][le]);else s.deleteFramebuffer(M.__webglFramebuffer[te]);M.__webglDepthbuffer&&s.deleteRenderbuffer(M.__webglDepthbuffer[te])}else{if(Array.isArray(M.__webglFramebuffer))for(let te=0;te<M.__webglFramebuffer.length;te++)s.deleteFramebuffer(M.__webglFramebuffer[te]);else s.deleteFramebuffer(M.__webglFramebuffer);if(M.__webglDepthbuffer&&s.deleteRenderbuffer(M.__webglDepthbuffer),M.__webglMultisampledFramebuffer&&s.deleteFramebuffer(M.__webglMultisampledFramebuffer),M.__webglColorRenderbuffer)for(let te=0;te<M.__webglColorRenderbuffer.length;te++)M.__webglColorRenderbuffer[te]&&s.deleteRenderbuffer(M.__webglColorRenderbuffer[te]);M.__webglDepthRenderbuffer&&s.deleteRenderbuffer(M.__webglDepthRenderbuffer)}const Y=w.textures;for(let te=0,le=Y.length;te<le;te++){const ye=r.get(Y[te]);ye.__webglTexture&&(s.deleteTexture(ye.__webglTexture),d.memory.textures--),r.remove(Y[te])}r.remove(w)}let Z=0;function de(){Z=0}function he(){return Z}function K(w){Z=w}function N(){const w=Z;return w>=l.maxTextures&&et("WebGLTextures: Trying to use "+w+" texture units while this GPU supports only "+l.maxTextures),Z+=1,w}function I(w){const M=[];return M.push(w.wrapS),M.push(w.wrapT),M.push(w.wrapR||0),M.push(w.magFilter),M.push(w.minFilter),M.push(w.anisotropy),M.push(w.internalFormat),M.push(w.format),M.push(w.type),M.push(w.generateMipmaps),M.push(w.premultiplyAlpha),M.push(w.flipY),M.push(w.unpackAlignment),M.push(w.colorSpace),M.join()}function $(w,M){const Y=r.get(w);if(w.isVideoTexture&&X(w),w.isRenderTargetTexture===!1&&w.isExternalTexture!==!0&&w.version>0&&Y.__version!==w.version){const te=w.image;if(te===null)et("WebGLRenderer: Texture marked for update but no image data found.");else if(te.complete===!1)et("WebGLRenderer: Texture marked for update but image is incomplete");else{$e(Y,w,M);return}}else w.isExternalTexture&&(Y.__webglTexture=w.sourceTexture?w.sourceTexture:null);i.bindTexture(s.TEXTURE_2D,Y.__webglTexture,s.TEXTURE0+M)}function se(w,M){const Y=r.get(w);if(w.isRenderTargetTexture===!1&&w.version>0&&Y.__version!==w.version){$e(Y,w,M);return}else w.isExternalTexture&&(Y.__webglTexture=w.sourceTexture?w.sourceTexture:null);i.bindTexture(s.TEXTURE_2D_ARRAY,Y.__webglTexture,s.TEXTURE0+M)}function pe(w,M){const Y=r.get(w);if(w.isRenderTargetTexture===!1&&w.version>0&&Y.__version!==w.version){$e(Y,w,M);return}i.bindTexture(s.TEXTURE_3D,Y.__webglTexture,s.TEXTURE0+M)}function xe(w,M){const Y=r.get(w);if(w.isCubeDepthTexture!==!0&&w.version>0&&Y.__version!==w.version){at(Y,w,M);return}i.bindTexture(s.TEXTURE_CUBE_MAP,Y.__webglTexture,s.TEXTURE0+M)}const Se={[ih]:s.REPEAT,[Sa]:s.CLAMP_TO_EDGE,[ah]:s.MIRRORED_REPEAT},Te={[Dn]:s.NEAREST,[My]:s.NEAREST_MIPMAP_NEAREST,[gc]:s.NEAREST_MIPMAP_LINEAR,[On]:s.LINEAR,[pd]:s.LINEAR_MIPMAP_NEAREST,[Fs]:s.LINEAR_MIPMAP_LINEAR},tt={[by]:s.NEVER,[wy]:s.ALWAYS,[Ty]:s.LESS,[Wh]:s.LEQUAL,[Ay]:s.EQUAL,[qh]:s.GEQUAL,[Ry]:s.GREATER,[Cy]:s.NOTEQUAL};function nt(w,M){if(M.type===Vi&&e.has("OES_texture_float_linear")===!1&&(M.magFilter===On||M.magFilter===pd||M.magFilter===gc||M.magFilter===Fs||M.minFilter===On||M.minFilter===pd||M.minFilter===gc||M.minFilter===Fs)&&et("WebGLRenderer: Unable to use linear filtering with floating point textures. OES_texture_float_linear not supported on this device."),s.texParameteri(w,s.TEXTURE_WRAP_S,Se[M.wrapS]),s.texParameteri(w,s.TEXTURE_WRAP_T,Se[M.wrapT]),(w===s.TEXTURE_3D||w===s.TEXTURE_2D_ARRAY)&&s.texParameteri(w,s.TEXTURE_WRAP_R,Se[M.wrapR]),s.texParameteri(w,s.TEXTURE_MAG_FILTER,Te[M.magFilter]),s.texParameteri(w,s.TEXTURE_MIN_FILTER,Te[M.minFilter]),M.compareFunction&&(s.texParameteri(w,s.TEXTURE_COMPARE_MODE,s.COMPARE_REF_TO_TEXTURE),s.texParameteri(w,s.TEXTURE_COMPARE_FUNC,tt[M.compareFunction])),e.has("EXT_texture_filter_anisotropic")===!0){if(M.magFilter===Dn||M.minFilter!==gc&&M.minFilter!==Fs||M.type===Vi&&e.has("OES_texture_float_linear")===!1)return;if(M.anisotropy>1||r.get(M).__currentAnisotropy){const Y=e.get("EXT_texture_filter_anisotropic");s.texParameterf(w,Y.TEXTURE_MAX_ANISOTROPY_EXT,Math.min(M.anisotropy,l.getMaxAnisotropy())),r.get(M).__currentAnisotropy=M.anisotropy}}}function ue(w,M){let Y=!1;w.__webglInit===void 0&&(w.__webglInit=!0,M.addEventListener("dispose",B));const te=M.source;let le=E.get(te);le===void 0&&(le={},E.set(te,le));const ye=I(M);if(ye!==w.__cacheKey){le[ye]===void 0&&(le[ye]={texture:s.createTexture(),usedTimes:0},d.memory.textures++,Y=!0),le[ye].usedTimes++;const Ce=le[w.__cacheKey];Ce!==void 0&&(le[w.__cacheKey].usedTimes--,Ce.usedTimes===0&&W(M)),w.__cacheKey=ye,w.__webglTexture=le[ye].texture}return Y}function Ae(w,M,Y){return Math.floor(Math.floor(w/Y)/M)}function Me(w,M,Y,te){const ye=w.updateRanges;if(ye.length===0)i.texSubImage2D(s.TEXTURE_2D,0,0,0,M.width,M.height,Y,te,M.data);else{ye.sort((Pe,Ue)=>Pe.start-Ue.start);let Ce=0;for(let Pe=1;Pe<ye.length;Pe++){const Ue=ye[Ce],we=ye[Pe],Ye=Ue.start+Ue.count,Ke=Ae(we.start,M.width,4),it=Ae(Ue.start,M.width,4);we.start<=Ye+1&&Ke===it&&Ae(we.start+we.count-1,M.width,4)===Ke?Ue.count=Math.max(Ue.count,we.start+we.count-Ue.start):(++Ce,ye[Ce]=we)}ye.length=Ce+1;const oe=i.getParameter(s.UNPACK_ROW_LENGTH),ce=i.getParameter(s.UNPACK_SKIP_PIXELS),be=i.getParameter(s.UNPACK_SKIP_ROWS);i.pixelStorei(s.UNPACK_ROW_LENGTH,M.width);for(let Pe=0,Ue=ye.length;Pe<Ue;Pe++){const we=ye[Pe],Ye=Math.floor(we.start/4),Ke=Math.ceil(we.count/4),it=Ye%M.width,G=Math.floor(Ye/M.width),Ee=Ke,me=1;i.pixelStorei(s.UNPACK_SKIP_PIXELS,it),i.pixelStorei(s.UNPACK_SKIP_ROWS,G),i.texSubImage2D(s.TEXTURE_2D,0,it,G,Ee,me,Y,te,M.data)}w.clearUpdateRanges(),i.pixelStorei(s.UNPACK_ROW_LENGTH,oe),i.pixelStorei(s.UNPACK_SKIP_PIXELS,ce),i.pixelStorei(s.UNPACK_SKIP_ROWS,be)}}function $e(w,M,Y){let te=s.TEXTURE_2D;(M.isDataArrayTexture||M.isCompressedArrayTexture)&&(te=s.TEXTURE_2D_ARRAY),M.isData3DTexture&&(te=s.TEXTURE_3D);const le=ue(w,M),ye=M.source;i.bindTexture(te,w.__webglTexture,s.TEXTURE0+Y);const Ce=r.get(ye);if(ye.version!==Ce.__version||le===!0){if(i.activeTexture(s.TEXTURE0+Y),(typeof ImageBitmap<"u"&&M.image instanceof ImageBitmap)===!1){const me=yt.getPrimaries(yt.workingColorSpace),Re=M.colorSpace===rs?null:yt.getPrimaries(M.colorSpace),Oe=M.colorSpace===rs||me===Re?s.NONE:s.BROWSER_DEFAULT_WEBGL;i.pixelStorei(s.UNPACK_FLIP_Y_WEBGL,M.flipY),i.pixelStorei(s.UNPACK_PREMULTIPLY_ALPHA_WEBGL,M.premultiplyAlpha),i.pixelStorei(s.UNPACK_COLORSPACE_CONVERSION_WEBGL,Oe)}i.pixelStorei(s.UNPACK_ALIGNMENT,M.unpackAlignment);let ce=y(M.image,!1,l.maxTextureSize);ce=Ht(M,ce);const be=u.convert(M.format,M.colorSpace),Pe=u.convert(M.type);let Ue=C(M.internalFormat,be,Pe,M.normalized,M.colorSpace,M.isVideoTexture);nt(te,M);let we;const Ye=M.mipmaps,Ke=M.isVideoTexture!==!0,it=Ce.__version===void 0||le===!0,G=ye.dataReady,Ee=U(M,ce);if(M.isDepthTexture)Ue=O(M.format===Bs,M.type),it&&(Ke?i.texStorage2D(s.TEXTURE_2D,1,Ue,ce.width,ce.height):i.texImage2D(s.TEXTURE_2D,0,Ue,ce.width,ce.height,0,be,Pe,null));else if(M.isDataTexture)if(Ye.length>0){Ke&&it&&i.texStorage2D(s.TEXTURE_2D,Ee,Ue,Ye[0].width,Ye[0].height);for(let me=0,Re=Ye.length;me<Re;me++)we=Ye[me],Ke?G&&i.texSubImage2D(s.TEXTURE_2D,me,0,0,we.width,we.height,be,Pe,we.data):i.texImage2D(s.TEXTURE_2D,me,Ue,we.width,we.height,0,be,Pe,we.data);M.generateMipmaps=!1}else Ke?(it&&i.texStorage2D(s.TEXTURE_2D,Ee,Ue,ce.width,ce.height),G&&Me(M,ce,be,Pe)):i.texImage2D(s.TEXTURE_2D,0,Ue,ce.width,ce.height,0,be,Pe,ce.data);else if(M.isCompressedTexture)if(M.isCompressedArrayTexture){Ke&&it&&i.texStorage3D(s.TEXTURE_2D_ARRAY,Ee,Ue,Ye[0].width,Ye[0].height,ce.depth);for(let me=0,Re=Ye.length;me<Re;me++)if(we=Ye[me],M.format!==Di)if(be!==null)if(Ke){if(G)if(M.layerUpdates.size>0){const Oe=E_(we.width,we.height,M.format,M.type);for(const ve of M.layerUpdates){const ke=we.data.subarray(ve*Oe/we.data.BYTES_PER_ELEMENT,(ve+1)*Oe/we.data.BYTES_PER_ELEMENT);i.compressedTexSubImage3D(s.TEXTURE_2D_ARRAY,me,0,0,ve,we.width,we.height,1,be,ke)}M.clearLayerUpdates()}else i.compressedTexSubImage3D(s.TEXTURE_2D_ARRAY,me,0,0,0,we.width,we.height,ce.depth,be,we.data)}else i.compressedTexImage3D(s.TEXTURE_2D_ARRAY,me,Ue,we.width,we.height,ce.depth,0,we.data,0,0);else et("WebGLRenderer: Attempt to load unsupported compressed texture format in .uploadTexture()");else Ke?G&&i.texSubImage3D(s.TEXTURE_2D_ARRAY,me,0,0,0,we.width,we.height,ce.depth,be,Pe,we.data):i.texImage3D(s.TEXTURE_2D_ARRAY,me,Ue,we.width,we.height,ce.depth,0,be,Pe,we.data)}else{Ke&&it&&i.texStorage2D(s.TEXTURE_2D,Ee,Ue,Ye[0].width,Ye[0].height);for(let me=0,Re=Ye.length;me<Re;me++)we=Ye[me],M.format!==Di?be!==null?Ke?G&&i.compressedTexSubImage2D(s.TEXTURE_2D,me,0,0,we.width,we.height,be,we.data):i.compressedTexImage2D(s.TEXTURE_2D,me,Ue,we.width,we.height,0,we.data):et("WebGLRenderer: Attempt to load unsupported compressed texture format in .uploadTexture()"):Ke?G&&i.texSubImage2D(s.TEXTURE_2D,me,0,0,we.width,we.height,be,Pe,we.data):i.texImage2D(s.TEXTURE_2D,me,Ue,we.width,we.height,0,be,Pe,we.data)}else if(M.isDataArrayTexture)if(Ke){if(it&&i.texStorage3D(s.TEXTURE_2D_ARRAY,Ee,Ue,ce.width,ce.height,ce.depth),G)if(M.layerUpdates.size>0){const me=E_(ce.width,ce.height,M.format,M.type);for(const Re of M.layerUpdates){const Oe=ce.data.subarray(Re*me/ce.data.BYTES_PER_ELEMENT,(Re+1)*me/ce.data.BYTES_PER_ELEMENT);i.texSubImage3D(s.TEXTURE_2D_ARRAY,0,0,0,Re,ce.width,ce.height,1,be,Pe,Oe)}M.clearLayerUpdates()}else i.texSubImage3D(s.TEXTURE_2D_ARRAY,0,0,0,0,ce.width,ce.height,ce.depth,be,Pe,ce.data)}else i.texImage3D(s.TEXTURE_2D_ARRAY,0,Ue,ce.width,ce.height,ce.depth,0,be,Pe,ce.data);else if(M.isData3DTexture)Ke?(it&&i.texStorage3D(s.TEXTURE_3D,Ee,Ue,ce.width,ce.height,ce.depth),G&&i.texSubImage3D(s.TEXTURE_3D,0,0,0,0,ce.width,ce.height,ce.depth,be,Pe,ce.data)):i.texImage3D(s.TEXTURE_3D,0,Ue,ce.width,ce.height,ce.depth,0,be,Pe,ce.data);else if(M.isFramebufferTexture){if(it)if(Ke)i.texStorage2D(s.TEXTURE_2D,Ee,Ue,ce.width,ce.height);else{let me=ce.width,Re=ce.height;for(let Oe=0;Oe<Ee;Oe++)i.texImage2D(s.TEXTURE_2D,Oe,Ue,me,Re,0,be,Pe,null),me>>=1,Re>>=1}}else if(M.isHTMLTexture){if("texElementImage2D"in s){const me=s.canvas;if(me.hasAttribute("layoutsubtree")||me.setAttribute("layoutsubtree","true"),ce.parentNode!==me){me.appendChild(ce),x.add(M),me.onpaint=Re=>{const Oe=Re.changedElements;for(const ve of x)Oe.includes(ve.image)&&(ve.needsUpdate=!0)},me.requestPaint();return}if(s.texElementImage2D.length===3)s.texElementImage2D(s.TEXTURE_2D,s.RGBA8,ce);else{const Oe=s.RGBA,ve=s.RGBA,ke=s.UNSIGNED_BYTE;s.texElementImage2D(s.TEXTURE_2D,0,Oe,ve,ke,ce)}s.texParameteri(s.TEXTURE_2D,s.TEXTURE_MIN_FILTER,s.LINEAR),s.texParameteri(s.TEXTURE_2D,s.TEXTURE_WRAP_S,s.CLAMP_TO_EDGE),s.texParameteri(s.TEXTURE_2D,s.TEXTURE_WRAP_T,s.CLAMP_TO_EDGE)}}else if(Ye.length>0){if(Ke&&it){const me=wt(Ye[0]);i.texStorage2D(s.TEXTURE_2D,Ee,Ue,me.width,me.height)}for(let me=0,Re=Ye.length;me<Re;me++)we=Ye[me],Ke?G&&i.texSubImage2D(s.TEXTURE_2D,me,0,0,be,Pe,we):i.texImage2D(s.TEXTURE_2D,me,Ue,be,Pe,we);M.generateMipmaps=!1}else if(Ke){if(it){const me=wt(ce);i.texStorage2D(s.TEXTURE_2D,Ee,Ue,me.width,me.height)}G&&i.texSubImage2D(s.TEXTURE_2D,0,0,0,be,Pe,ce)}else i.texImage2D(s.TEXTURE_2D,0,Ue,be,Pe,ce);S(M)&&z(te),Ce.__version=ye.version,M.onUpdate&&M.onUpdate(M)}w.__version=M.version}function at(w,M,Y){if(M.image.length!==6)return;const te=ue(w,M),le=M.source;i.bindTexture(s.TEXTURE_CUBE_MAP,w.__webglTexture,s.TEXTURE0+Y);const ye=r.get(le);if(le.version!==ye.__version||te===!0){i.activeTexture(s.TEXTURE0+Y);const Ce=yt.getPrimaries(yt.workingColorSpace),oe=M.colorSpace===rs?null:yt.getPrimaries(M.colorSpace),ce=M.colorSpace===rs||Ce===oe?s.NONE:s.BROWSER_DEFAULT_WEBGL;i.pixelStorei(s.UNPACK_FLIP_Y_WEBGL,M.flipY),i.pixelStorei(s.UNPACK_PREMULTIPLY_ALPHA_WEBGL,M.premultiplyAlpha),i.pixelStorei(s.UNPACK_ALIGNMENT,M.unpackAlignment),i.pixelStorei(s.UNPACK_COLORSPACE_CONVERSION_WEBGL,ce);const be=M.isCompressedTexture||M.image[0].isCompressedTexture,Pe=M.image[0]&&M.image[0].isDataTexture,Ue=[];for(let ve=0;ve<6;ve++)!be&&!Pe?Ue[ve]=y(M.image[ve],!0,l.maxCubemapSize):Ue[ve]=Pe?M.image[ve].image:M.image[ve],Ue[ve]=Ht(M,Ue[ve]);const we=Ue[0],Ye=u.convert(M.format,M.colorSpace),Ke=u.convert(M.type),it=C(M.internalFormat,Ye,Ke,M.normalized,M.colorSpace),G=M.isVideoTexture!==!0,Ee=ye.__version===void 0||te===!0,me=le.dataReady;let Re=U(M,we);nt(s.TEXTURE_CUBE_MAP,M);let Oe;if(be){G&&Ee&&i.texStorage2D(s.TEXTURE_CUBE_MAP,Re,it,we.width,we.height);for(let ve=0;ve<6;ve++){Oe=Ue[ve].mipmaps;for(let ke=0;ke<Oe.length;ke++){const Ie=Oe[ke];M.format!==Di?Ye!==null?G?me&&i.compressedTexSubImage2D(s.TEXTURE_CUBE_MAP_POSITIVE_X+ve,ke,0,0,Ie.width,Ie.height,Ye,Ie.data):i.compressedTexImage2D(s.TEXTURE_CUBE_MAP_POSITIVE_X+ve,ke,it,Ie.width,Ie.height,0,Ie.data):et("WebGLRenderer: Attempt to load unsupported compressed texture format in .setTextureCube()"):G?me&&i.texSubImage2D(s.TEXTURE_CUBE_MAP_POSITIVE_X+ve,ke,0,0,Ie.width,Ie.height,Ye,Ke,Ie.data):i.texImage2D(s.TEXTURE_CUBE_MAP_POSITIVE_X+ve,ke,it,Ie.width,Ie.height,0,Ye,Ke,Ie.data)}}}else{if(Oe=M.mipmaps,G&&Ee){Oe.length>0&&Re++;const ve=wt(Ue[0]);i.texStorage2D(s.TEXTURE_CUBE_MAP,Re,it,ve.width,ve.height)}for(let ve=0;ve<6;ve++)if(Pe){G?me&&i.texSubImage2D(s.TEXTURE_CUBE_MAP_POSITIVE_X+ve,0,0,0,Ue[ve].width,Ue[ve].height,Ye,Ke,Ue[ve].data):i.texImage2D(s.TEXTURE_CUBE_MAP_POSITIVE_X+ve,0,it,Ue[ve].width,Ue[ve].height,0,Ye,Ke,Ue[ve].data);for(let ke=0;ke<Oe.length;ke++){const Qt=Oe[ke].image[ve].image;G?me&&i.texSubImage2D(s.TEXTURE_CUBE_MAP_POSITIVE_X+ve,ke+1,0,0,Qt.width,Qt.height,Ye,Ke,Qt.data):i.texImage2D(s.TEXTURE_CUBE_MAP_POSITIVE_X+ve,ke+1,it,Qt.width,Qt.height,0,Ye,Ke,Qt.data)}}else{G?me&&i.texSubImage2D(s.TEXTURE_CUBE_MAP_POSITIVE_X+ve,0,0,0,Ye,Ke,Ue[ve]):i.texImage2D(s.TEXTURE_CUBE_MAP_POSITIVE_X+ve,0,it,Ye,Ke,Ue[ve]);for(let ke=0;ke<Oe.length;ke++){const Ie=Oe[ke];G?me&&i.texSubImage2D(s.TEXTURE_CUBE_MAP_POSITIVE_X+ve,ke+1,0,0,Ye,Ke,Ie.image[ve]):i.texImage2D(s.TEXTURE_CUBE_MAP_POSITIVE_X+ve,ke+1,it,Ye,Ke,Ie.image[ve])}}}S(M)&&z(s.TEXTURE_CUBE_MAP),ye.__version=le.version,M.onUpdate&&M.onUpdate(M)}w.__version=M.version}function Ze(w,M,Y,te,le,ye){const Ce=u.convert(Y.format,Y.colorSpace),oe=u.convert(Y.type),ce=C(Y.internalFormat,Ce,oe,Y.normalized,Y.colorSpace),be=r.get(M),Pe=r.get(Y);if(Pe.__renderTarget=M,!be.__hasExternalTextures){const Ue=Math.max(1,M.width>>ye),we=Math.max(1,M.height>>ye);le===s.TEXTURE_3D||le===s.TEXTURE_2D_ARRAY?i.texImage3D(le,ye,ce,Ue,we,M.depth,0,Ce,oe,null):i.texImage2D(le,ye,ce,Ue,we,0,Ce,oe,null)}i.bindFramebuffer(s.FRAMEBUFFER,w),an(M)?h.framebufferTexture2DMultisampleEXT(s.FRAMEBUFFER,te,le,Pe.__webglTexture,0,qt(M)):(le===s.TEXTURE_2D||le>=s.TEXTURE_CUBE_MAP_POSITIVE_X&&le<=s.TEXTURE_CUBE_MAP_NEGATIVE_Z)&&s.framebufferTexture2D(s.FRAMEBUFFER,te,le,Pe.__webglTexture,ye),i.bindFramebuffer(s.FRAMEBUFFER,null)}function Yt(w,M,Y){if(s.bindRenderbuffer(s.RENDERBUFFER,w),M.depthBuffer){const te=M.depthTexture,le=te&&te.isDepthTexture?te.type:null,ye=O(M.stencilBuffer,le),Ce=M.stencilBuffer?s.DEPTH_STENCIL_ATTACHMENT:s.DEPTH_ATTACHMENT;an(M)?h.renderbufferStorageMultisampleEXT(s.RENDERBUFFER,qt(M),ye,M.width,M.height):Y?s.renderbufferStorageMultisample(s.RENDERBUFFER,qt(M),ye,M.width,M.height):s.renderbufferStorage(s.RENDERBUFFER,ye,M.width,M.height),s.framebufferRenderbuffer(s.FRAMEBUFFER,Ce,s.RENDERBUFFER,w)}else{const te=M.textures;for(let le=0;le<te.length;le++){const ye=te[le],Ce=u.convert(ye.format,ye.colorSpace),oe=u.convert(ye.type),ce=C(ye.internalFormat,Ce,oe,ye.normalized,ye.colorSpace);an(M)?h.renderbufferStorageMultisampleEXT(s.RENDERBUFFER,qt(M),ce,M.width,M.height):Y?s.renderbufferStorageMultisample(s.RENDERBUFFER,qt(M),ce,M.width,M.height):s.renderbufferStorage(s.RENDERBUFFER,ce,M.width,M.height)}}s.bindRenderbuffer(s.RENDERBUFFER,null)}function ut(w,M,Y){const te=M.isWebGLCubeRenderTarget===!0;if(i.bindFramebuffer(s.FRAMEBUFFER,w),!(M.depthTexture&&M.depthTexture.isDepthTexture))throw new Error("THREE.WebGLTextures: renderTarget.depthTexture must be an instance of THREE.DepthTexture.");const le=r.get(M.depthTexture);if(le.__renderTarget=M,(!le.__webglTexture||M.depthTexture.image.width!==M.width||M.depthTexture.image.height!==M.height)&&(M.depthTexture.image.width=M.width,M.depthTexture.image.height=M.height,M.depthTexture.needsUpdate=!0),te){if(le.__webglInit===void 0&&(le.__webglInit=!0,M.depthTexture.addEventListener("dispose",B)),le.__webglTexture===void 0){le.__webglTexture=s.createTexture(),i.bindTexture(s.TEXTURE_CUBE_MAP,le.__webglTexture),nt(s.TEXTURE_CUBE_MAP,M.depthTexture);const be=u.convert(M.depthTexture.format),Pe=u.convert(M.depthTexture.type);let Ue;M.depthTexture.format===Ta?Ue=s.DEPTH_COMPONENT24:M.depthTexture.format===Bs&&(Ue=s.DEPTH24_STENCIL8);for(let we=0;we<6;we++)s.texImage2D(s.TEXTURE_CUBE_MAP_POSITIVE_X+we,0,Ue,M.width,M.height,0,be,Pe,null)}}else $(M.depthTexture,0);const ye=le.__webglTexture,Ce=qt(M),oe=te?s.TEXTURE_CUBE_MAP_POSITIVE_X+Y:s.TEXTURE_2D,ce=M.depthTexture.format===Bs?s.DEPTH_STENCIL_ATTACHMENT:s.DEPTH_ATTACHMENT;if(M.depthTexture.format===Ta)an(M)?h.framebufferTexture2DMultisampleEXT(s.FRAMEBUFFER,ce,oe,ye,0,Ce):s.framebufferTexture2D(s.FRAMEBUFFER,ce,oe,ye,0);else if(M.depthTexture.format===Bs)an(M)?h.framebufferTexture2DMultisampleEXT(s.FRAMEBUFFER,ce,oe,ye,0,Ce):s.framebufferTexture2D(s.FRAMEBUFFER,ce,oe,ye,0);else throw new Error("THREE.WebGLTextures: Unknown depthTexture format.")}function xt(w){const M=r.get(w),Y=w.isWebGLCubeRenderTarget===!0;if(M.__boundDepthTexture!==w.depthTexture){const te=w.depthTexture;if(M.__depthDisposeCallback&&M.__depthDisposeCallback(),te){const le=()=>{delete M.__boundDepthTexture,delete M.__depthDisposeCallback,te.removeEventListener("dispose",le)};te.addEventListener("dispose",le),M.__depthDisposeCallback=le}M.__boundDepthTexture=te}if(w.depthTexture&&!M.__autoAllocateDepthBuffer)if(Y)for(let te=0;te<6;te++)ut(M.__webglFramebuffer[te],w,te);else{const te=w.texture.mipmaps;te&&te.length>0?ut(M.__webglFramebuffer[0],w,0):ut(M.__webglFramebuffer,w,0)}else if(Y){M.__webglDepthbuffer=[];for(let te=0;te<6;te++)if(i.bindFramebuffer(s.FRAMEBUFFER,M.__webglFramebuffer[te]),M.__webglDepthbuffer[te]===void 0)M.__webglDepthbuffer[te]=s.createRenderbuffer(),Yt(M.__webglDepthbuffer[te],w,!1);else{const le=w.stencilBuffer?s.DEPTH_STENCIL_ATTACHMENT:s.DEPTH_ATTACHMENT,ye=M.__webglDepthbuffer[te];s.bindRenderbuffer(s.RENDERBUFFER,ye),s.framebufferRenderbuffer(s.FRAMEBUFFER,le,s.RENDERBUFFER,ye)}}else{const te=w.texture.mipmaps;if(te&&te.length>0?i.bindFramebuffer(s.FRAMEBUFFER,M.__webglFramebuffer[0]):i.bindFramebuffer(s.FRAMEBUFFER,M.__webglFramebuffer),M.__webglDepthbuffer===void 0)M.__webglDepthbuffer=s.createRenderbuffer(),Yt(M.__webglDepthbuffer,w,!1);else{const le=w.stencilBuffer?s.DEPTH_STENCIL_ATTACHMENT:s.DEPTH_ATTACHMENT,ye=M.__webglDepthbuffer;s.bindRenderbuffer(s.RENDERBUFFER,ye),s.framebufferRenderbuffer(s.FRAMEBUFFER,le,s.RENDERBUFFER,ye)}}i.bindFramebuffer(s.FRAMEBUFFER,null)}function St(w,M,Y){const te=r.get(w);M!==void 0&&Ze(te.__webglFramebuffer,w,w.texture,s.COLOR_ATTACHMENT0,s.TEXTURE_2D,0),Y!==void 0&&xt(w)}function ft(w){const M=w.texture,Y=r.get(w),te=r.get(M);w.addEventListener("dispose",b);const le=w.textures,ye=w.isWebGLCubeRenderTarget===!0,Ce=le.length>1;if(Ce||(te.__webglTexture===void 0&&(te.__webglTexture=s.createTexture()),te.__version=M.version,d.memory.textures++),ye){Y.__webglFramebuffer=[];for(let oe=0;oe<6;oe++)if(M.mipmaps&&M.mipmaps.length>0){Y.__webglFramebuffer[oe]=[];for(let ce=0;ce<M.mipmaps.length;ce++)Y.__webglFramebuffer[oe][ce]=s.createFramebuffer()}else Y.__webglFramebuffer[oe]=s.createFramebuffer()}else{if(M.mipmaps&&M.mipmaps.length>0){Y.__webglFramebuffer=[];for(let oe=0;oe<M.mipmaps.length;oe++)Y.__webglFramebuffer[oe]=s.createFramebuffer()}else Y.__webglFramebuffer=s.createFramebuffer();if(Ce)for(let oe=0,ce=le.length;oe<ce;oe++){const be=r.get(le[oe]);be.__webglTexture===void 0&&(be.__webglTexture=s.createTexture(),d.memory.textures++)}if(w.samples>0&&an(w)===!1){Y.__webglMultisampledFramebuffer=s.createFramebuffer(),Y.__webglColorRenderbuffer=[],i.bindFramebuffer(s.FRAMEBUFFER,Y.__webglMultisampledFramebuffer);for(let oe=0;oe<le.length;oe++){const ce=le[oe];Y.__webglColorRenderbuffer[oe]=s.createRenderbuffer(),s.bindRenderbuffer(s.RENDERBUFFER,Y.__webglColorRenderbuffer[oe]);const be=u.convert(ce.format,ce.colorSpace),Pe=u.convert(ce.type),Ue=C(ce.internalFormat,be,Pe,ce.normalized,ce.colorSpace,w.isXRRenderTarget===!0),we=qt(w);s.renderbufferStorageMultisample(s.RENDERBUFFER,we,Ue,w.width,w.height),s.framebufferRenderbuffer(s.FRAMEBUFFER,s.COLOR_ATTACHMENT0+oe,s.RENDERBUFFER,Y.__webglColorRenderbuffer[oe])}s.bindRenderbuffer(s.RENDERBUFFER,null),w.depthBuffer&&(Y.__webglDepthRenderbuffer=s.createRenderbuffer(),Yt(Y.__webglDepthRenderbuffer,w,!0)),i.bindFramebuffer(s.FRAMEBUFFER,null)}}if(ye){i.bindTexture(s.TEXTURE_CUBE_MAP,te.__webglTexture),nt(s.TEXTURE_CUBE_MAP,M);for(let oe=0;oe<6;oe++)if(M.mipmaps&&M.mipmaps.length>0)for(let ce=0;ce<M.mipmaps.length;ce++)Ze(Y.__webglFramebuffer[oe][ce],w,M,s.COLOR_ATTACHMENT0,s.TEXTURE_CUBE_MAP_POSITIVE_X+oe,ce);else Ze(Y.__webglFramebuffer[oe],w,M,s.COLOR_ATTACHMENT0,s.TEXTURE_CUBE_MAP_POSITIVE_X+oe,0);S(M)&&z(s.TEXTURE_CUBE_MAP),i.unbindTexture()}else if(Ce){for(let oe=0,ce=le.length;oe<ce;oe++){const be=le[oe],Pe=r.get(be);let Ue=s.TEXTURE_2D;(w.isWebGL3DRenderTarget||w.isWebGLArrayRenderTarget)&&(Ue=w.isWebGL3DRenderTarget?s.TEXTURE_3D:s.TEXTURE_2D_ARRAY),i.bindTexture(Ue,Pe.__webglTexture),nt(Ue,be),Ze(Y.__webglFramebuffer,w,be,s.COLOR_ATTACHMENT0+oe,Ue,0),S(be)&&z(Ue)}i.unbindTexture()}else{let oe=s.TEXTURE_2D;if((w.isWebGL3DRenderTarget||w.isWebGLArrayRenderTarget)&&(oe=w.isWebGL3DRenderTarget?s.TEXTURE_3D:s.TEXTURE_2D_ARRAY),i.bindTexture(oe,te.__webglTexture),nt(oe,M),M.mipmaps&&M.mipmaps.length>0)for(let ce=0;ce<M.mipmaps.length;ce++)Ze(Y.__webglFramebuffer[ce],w,M,s.COLOR_ATTACHMENT0,oe,ce);else Ze(Y.__webglFramebuffer,w,M,s.COLOR_ATTACHMENT0,oe,0);S(M)&&z(oe),i.unbindTexture()}w.depthBuffer&&xt(w)}function en(w){const M=w.textures;for(let Y=0,te=M.length;Y<te;Y++){const le=M[Y];if(S(le)){const ye=F(w),Ce=r.get(le).__webglTexture;i.bindTexture(ye,Ce),z(ye),i.unbindTexture()}}}const tn=[],nn=[];function ln(w){if(w.samples>0){if(an(w)===!1){const M=w.textures,Y=w.width,te=w.height;let le=s.COLOR_BUFFER_BIT;const ye=w.stencilBuffer?s.DEPTH_STENCIL_ATTACHMENT:s.DEPTH_ATTACHMENT,Ce=r.get(w),oe=M.length>1;if(oe)for(let be=0;be<M.length;be++)i.bindFramebuffer(s.FRAMEBUFFER,Ce.__webglMultisampledFramebuffer),s.framebufferRenderbuffer(s.FRAMEBUFFER,s.COLOR_ATTACHMENT0+be,s.RENDERBUFFER,null),i.bindFramebuffer(s.FRAMEBUFFER,Ce.__webglFramebuffer),s.framebufferTexture2D(s.DRAW_FRAMEBUFFER,s.COLOR_ATTACHMENT0+be,s.TEXTURE_2D,null,0);i.bindFramebuffer(s.READ_FRAMEBUFFER,Ce.__webglMultisampledFramebuffer);const ce=w.texture.mipmaps;ce&&ce.length>0?i.bindFramebuffer(s.DRAW_FRAMEBUFFER,Ce.__webglFramebuffer[0]):i.bindFramebuffer(s.DRAW_FRAMEBUFFER,Ce.__webglFramebuffer);for(let be=0;be<M.length;be++){if(w.resolveDepthBuffer&&(w.depthBuffer&&(le|=s.DEPTH_BUFFER_BIT),w.stencilBuffer&&w.resolveStencilBuffer&&(le|=s.STENCIL_BUFFER_BIT)),oe){s.framebufferRenderbuffer(s.READ_FRAMEBUFFER,s.COLOR_ATTACHMENT0,s.RENDERBUFFER,Ce.__webglColorRenderbuffer[be]);const Pe=r.get(M[be]).__webglTexture;s.framebufferTexture2D(s.DRAW_FRAMEBUFFER,s.COLOR_ATTACHMENT0,s.TEXTURE_2D,Pe,0)}s.blitFramebuffer(0,0,Y,te,0,0,Y,te,le,s.NEAREST),m===!0&&(tn.length=0,nn.length=0,tn.push(s.COLOR_ATTACHMENT0+be),w.depthBuffer&&w.resolveDepthBuffer===!1&&(tn.push(ye),nn.push(ye),s.invalidateFramebuffer(s.DRAW_FRAMEBUFFER,nn)),s.invalidateFramebuffer(s.READ_FRAMEBUFFER,tn))}if(i.bindFramebuffer(s.READ_FRAMEBUFFER,null),i.bindFramebuffer(s.DRAW_FRAMEBUFFER,null),oe)for(let be=0;be<M.length;be++){i.bindFramebuffer(s.FRAMEBUFFER,Ce.__webglMultisampledFramebuffer),s.framebufferRenderbuffer(s.FRAMEBUFFER,s.COLOR_ATTACHMENT0+be,s.RENDERBUFFER,Ce.__webglColorRenderbuffer[be]);const Pe=r.get(M[be]).__webglTexture;i.bindFramebuffer(s.FRAMEBUFFER,Ce.__webglFramebuffer),s.framebufferTexture2D(s.DRAW_FRAMEBUFFER,s.COLOR_ATTACHMENT0+be,s.TEXTURE_2D,Pe,0)}i.bindFramebuffer(s.DRAW_FRAMEBUFFER,Ce.__webglMultisampledFramebuffer)}else if(w.depthBuffer&&w.resolveDepthBuffer===!1&&m){const M=w.stencilBuffer?s.DEPTH_STENCIL_ATTACHMENT:s.DEPTH_ATTACHMENT;s.invalidateFramebuffer(s.DRAW_FRAMEBUFFER,[M])}}}function qt(w){return Math.min(l.maxSamples,w.samples)}function an(w){const M=r.get(w);return w.samples>0&&e.has("WEBGL_multisampled_render_to_texture")===!0&&M.__useRenderToTexture!==!1}function X(w){const M=d.render.frame;v.get(w)!==M&&(v.set(w,M),w.update())}function Ht(w,M){const Y=w.colorSpace,te=w.format,le=w.type;return w.isCompressedTexture===!0||w.isVideoTexture===!0||Y!==Kc&&Y!==rs&&(yt.getTransfer(Y)===It?(te!==Di||le!==xi)&&et("WebGLTextures: sRGB encoded textures have to use RGBAFormat and UnsignedByteType."):bt("WebGLTextures: Unsupported texture color space:",Y)),M}function wt(w){return typeof HTMLImageElement<"u"&&w instanceof HTMLImageElement?(p.width=w.naturalWidth||w.width,p.height=w.naturalHeight||w.height):typeof VideoFrame<"u"&&w instanceof VideoFrame?(p.width=w.displayWidth,p.height=w.displayHeight):(p.width=w.width,p.height=w.height),p}this.allocateTextureUnit=N,this.resetTextureUnits=de,this.getTextureUnits=he,this.setTextureUnits=K,this.setTexture2D=$,this.setTexture2DArray=se,this.setTexture3D=pe,this.setTextureCube=xe,this.rebindTextures=St,this.setupRenderTarget=ft,this.updateRenderTargetMipmap=en,this.updateMultisampleRenderTarget=ln,this.setupDepthRenderbuffer=xt,this.setupFrameBufferTexture=Ze,this.useMultisampledRTT=an,this.isReversedDepthBuffer=function(){return i.buffers.depth.getReversed()}}function gR(s,e){function i(r,l=rs){let u;const d=yt.getTransfer(l);if(r===xi)return s.UNSIGNED_BYTE;if(r===Hh)return s.UNSIGNED_SHORT_4_4_4_4;if(r===Gh)return s.UNSIGNED_SHORT_5_5_5_1;if(r===hv)return s.UNSIGNED_INT_5_9_9_9_REV;if(r===pv)return s.UNSIGNED_INT_10F_11F_11F_REV;if(r===fv)return s.BYTE;if(r===dv)return s.SHORT;if(r===Jo)return s.UNSIGNED_SHORT;if(r===Ih)return s.INT;if(r===qi)return s.UNSIGNED_INT;if(r===Vi)return s.FLOAT;if(r===ba)return s.HALF_FLOAT;if(r===mv)return s.ALPHA;if(r===gv)return s.RGB;if(r===Di)return s.RGBA;if(r===Ta)return s.DEPTH_COMPONENT;if(r===Bs)return s.DEPTH_STENCIL;if(r===_v)return s.RED;if(r===Vh)return s.RED_INTEGER;if(r===Hs)return s.RG;if(r===kh)return s.RG_INTEGER;if(r===Xh)return s.RGBA_INTEGER;if(r===Vc||r===kc||r===Xc||r===Wc)if(d===It)if(u=e.get("WEBGL_compressed_texture_s3tc_srgb"),u!==null){if(r===Vc)return u.COMPRESSED_SRGB_S3TC_DXT1_EXT;if(r===kc)return u.COMPRESSED_SRGB_ALPHA_S3TC_DXT1_EXT;if(r===Xc)return u.COMPRESSED_SRGB_ALPHA_S3TC_DXT3_EXT;if(r===Wc)return u.COMPRESSED_SRGB_ALPHA_S3TC_DXT5_EXT}else return null;else if(u=e.get("WEBGL_compressed_texture_s3tc"),u!==null){if(r===Vc)return u.COMPRESSED_RGB_S3TC_DXT1_EXT;if(r===kc)return u.COMPRESSED_RGBA_S3TC_DXT1_EXT;if(r===Xc)return u.COMPRESSED_RGBA_S3TC_DXT3_EXT;if(r===Wc)return u.COMPRESSED_RGBA_S3TC_DXT5_EXT}else return null;if(r===sh||r===rh||r===oh||r===lh)if(u=e.get("WEBGL_compressed_texture_pvrtc"),u!==null){if(r===sh)return u.COMPRESSED_RGB_PVRTC_4BPPV1_IMG;if(r===rh)return u.COMPRESSED_RGB_PVRTC_2BPPV1_IMG;if(r===oh)return u.COMPRESSED_RGBA_PVRTC_4BPPV1_IMG;if(r===lh)return u.COMPRESSED_RGBA_PVRTC_2BPPV1_IMG}else return null;if(r===ch||r===uh||r===fh||r===dh||r===hh||r===Yc||r===ph)if(u=e.get("WEBGL_compressed_texture_etc"),u!==null){if(r===ch||r===uh)return d===It?u.COMPRESSED_SRGB8_ETC2:u.COMPRESSED_RGB8_ETC2;if(r===fh)return d===It?u.COMPRESSED_SRGB8_ALPHA8_ETC2_EAC:u.COMPRESSED_RGBA8_ETC2_EAC;if(r===dh)return u.COMPRESSED_R11_EAC;if(r===hh)return u.COMPRESSED_SIGNED_R11_EAC;if(r===Yc)return u.COMPRESSED_RG11_EAC;if(r===ph)return u.COMPRESSED_SIGNED_RG11_EAC}else return null;if(r===mh||r===gh||r===_h||r===vh||r===xh||r===Sh||r===Mh||r===yh||r===Eh||r===bh||r===Th||r===Ah||r===Rh||r===Ch)if(u=e.get("WEBGL_compressed_texture_astc"),u!==null){if(r===mh)return d===It?u.COMPRESSED_SRGB8_ALPHA8_ASTC_4x4_KHR:u.COMPRESSED_RGBA_ASTC_4x4_KHR;if(r===gh)return d===It?u.COMPRESSED_SRGB8_ALPHA8_ASTC_5x4_KHR:u.COMPRESSED_RGBA_ASTC_5x4_KHR;if(r===_h)return d===It?u.COMPRESSED_SRGB8_ALPHA8_ASTC_5x5_KHR:u.COMPRESSED_RGBA_ASTC_5x5_KHR;if(r===vh)return d===It?u.COMPRESSED_SRGB8_ALPHA8_ASTC_6x5_KHR:u.COMPRESSED_RGBA_ASTC_6x5_KHR;if(r===xh)return d===It?u.COMPRESSED_SRGB8_ALPHA8_ASTC_6x6_KHR:u.COMPRESSED_RGBA_ASTC_6x6_KHR;if(r===Sh)return d===It?u.COMPRESSED_SRGB8_ALPHA8_ASTC_8x5_KHR:u.COMPRESSED_RGBA_ASTC_8x5_KHR;if(r===Mh)return d===It?u.COMPRESSED_SRGB8_ALPHA8_ASTC_8x6_KHR:u.COMPRESSED_RGBA_ASTC_8x6_KHR;if(r===yh)return d===It?u.COMPRESSED_SRGB8_ALPHA8_ASTC_8x8_KHR:u.COMPRESSED_RGBA_ASTC_8x8_KHR;if(r===Eh)return d===It?u.COMPRESSED_SRGB8_ALPHA8_ASTC_10x5_KHR:u.COMPRESSED_RGBA_ASTC_10x5_KHR;if(r===bh)return d===It?u.COMPRESSED_SRGB8_ALPHA8_ASTC_10x6_KHR:u.COMPRESSED_RGBA_ASTC_10x6_KHR;if(r===Th)return d===It?u.COMPRESSED_SRGB8_ALPHA8_ASTC_10x8_KHR:u.COMPRESSED_RGBA_ASTC_10x8_KHR;if(r===Ah)return d===It?u.COMPRESSED_SRGB8_ALPHA8_ASTC_10x10_KHR:u.COMPRESSED_RGBA_ASTC_10x10_KHR;if(r===Rh)return d===It?u.COMPRESSED_SRGB8_ALPHA8_ASTC_12x10_KHR:u.COMPRESSED_RGBA_ASTC_12x10_KHR;if(r===Ch)return d===It?u.COMPRESSED_SRGB8_ALPHA8_ASTC_12x12_KHR:u.COMPRESSED_RGBA_ASTC_12x12_KHR}else return null;if(r===wh||r===Dh||r===Uh)if(u=e.get("EXT_texture_compression_bptc"),u!==null){if(r===wh)return d===It?u.COMPRESSED_SRGB_ALPHA_BPTC_UNORM_EXT:u.COMPRESSED_RGBA_BPTC_UNORM_EXT;if(r===Dh)return u.COMPRESSED_RGB_BPTC_SIGNED_FLOAT_EXT;if(r===Uh)return u.COMPRESSED_RGB_BPTC_UNSIGNED_FLOAT_EXT}else return null;if(r===Lh||r===Nh||r===Zc||r===Oh)if(u=e.get("EXT_texture_compression_rgtc"),u!==null){if(r===Lh)return u.COMPRESSED_RED_RGTC1_EXT;if(r===Nh)return u.COMPRESSED_SIGNED_RED_RGTC1_EXT;if(r===Zc)return u.COMPRESSED_RED_GREEN_RGTC2_EXT;if(r===Oh)return u.COMPRESSED_SIGNED_RED_GREEN_RGTC2_EXT}else return null;return r===$o?s.UNSIGNED_INT_24_8:s[r]!==void 0?s[r]:null}return{convert:i}}const _R=`
void main() {

	gl_Position = vec4( position, 1.0 );

}`,vR=`
uniform sampler2DArray depthColor;
uniform float depthWidth;
uniform float depthHeight;

void main() {

	vec2 coord = vec2( gl_FragCoord.x / depthWidth, gl_FragCoord.y / depthHeight );

	if ( coord.x >= 1.0 ) {

		gl_FragDepth = texture( depthColor, vec3( coord.x - 1.0, coord.y, 1 ) ).r;

	} else {

		gl_FragDepth = texture( depthColor, vec3( coord.x, coord.y, 0 ) ).r;

	}

}`;class xR{constructor(){this.texture=null,this.mesh=null,this.depthNear=0,this.depthFar=0}init(e,i){if(this.texture===null){const r=new Cv(e.texture);(e.depthNear!==i.depthNear||e.depthFar!==i.depthFar)&&(this.depthNear=e.depthNear,this.depthFar=e.depthFar),this.texture=r}}getMesh(e){if(this.texture!==null&&this.mesh===null){const i=e.cameras[0].viewport,r=new Yi({vertexShader:_R,fragmentShader:vR,uniforms:{depthColor:{value:this.texture},depthWidth:{value:i.z},depthHeight:{value:i.w}}});this.mesh=new Ui(new il(20,20),r)}return this.mesh}reset(){this.texture=null,this.mesh=null}getDepthTexture(){return this.texture}}class SR extends Vs{constructor(e,i){super();const r=this;let l=null,u=1,d=null,h="local-floor",m=1,p=null,v=null,x=null,g=null,E=null,T=null;const D=typeof XRWebGLBinding<"u",y=new xR,S={},z=i.getContextAttributes();let F=null,C=null;const O=[],U=[],B=new Ut;let b=null;const L=new vi;L.viewport=new on;const W=new vi;W.viewport=new on;const V=[L,W],Z=new wE;let de=null,he=null;this.cameraAutoUpdate=!0,this.enabled=!1,this.isPresenting=!1,this.getController=function(ue){let Ae=O[ue];return Ae===void 0&&(Ae=new Sd,O[ue]=Ae),Ae.getTargetRaySpace()},this.getControllerGrip=function(ue){let Ae=O[ue];return Ae===void 0&&(Ae=new Sd,O[ue]=Ae),Ae.getGripSpace()},this.getHand=function(ue){let Ae=O[ue];return Ae===void 0&&(Ae=new Sd,O[ue]=Ae),Ae.getHandSpace()};function K(ue){const Ae=U.indexOf(ue.inputSource);if(Ae===-1)return;const Me=O[Ae];Me!==void 0&&(Me.update(ue.inputSource,ue.frame,p||d),Me.dispatchEvent({type:ue.type,data:ue.inputSource}))}function N(){l.removeEventListener("select",K),l.removeEventListener("selectstart",K),l.removeEventListener("selectend",K),l.removeEventListener("squeeze",K),l.removeEventListener("squeezestart",K),l.removeEventListener("squeezeend",K),l.removeEventListener("end",N),l.removeEventListener("inputsourceschange",I);for(let ue=0;ue<O.length;ue++){const Ae=U[ue];Ae!==null&&(U[ue]=null,O[ue].disconnect(Ae))}de=null,he=null,y.reset();for(const ue in S)delete S[ue];e.setRenderTarget(F),E=null,g=null,x=null,l=null,C=null,nt.stop(),r.isPresenting=!1,e.setPixelRatio(b),e.setSize(B.width,B.height,!1),r.dispatchEvent({type:"sessionend"})}this.setFramebufferScaleFactor=function(ue){u=ue,r.isPresenting===!0&&et("WebXRManager: Cannot change framebuffer scale while presenting.")},this.setReferenceSpaceType=function(ue){h=ue,r.isPresenting===!0&&et("WebXRManager: Cannot change reference space type while presenting.")},this.getReferenceSpace=function(){return p||d},this.setReferenceSpace=function(ue){p=ue},this.getBaseLayer=function(){return g!==null?g:E},this.getBinding=function(){return x===null&&D&&(x=new XRWebGLBinding(l,i)),x},this.getFrame=function(){return T},this.getSession=function(){return l},this.setSession=async function(ue){if(l=ue,l!==null){if(F=e.getRenderTarget(),l.addEventListener("select",K),l.addEventListener("selectstart",K),l.addEventListener("selectend",K),l.addEventListener("squeeze",K),l.addEventListener("squeezestart",K),l.addEventListener("squeezeend",K),l.addEventListener("end",N),l.addEventListener("inputsourceschange",I),z.xrCompatible!==!0&&await i.makeXRCompatible(),b=e.getPixelRatio(),e.getSize(B),D&&"createProjectionLayer"in XRWebGLBinding.prototype){let Me=null,$e=null,at=null;z.depth&&(at=z.stencil?i.DEPTH24_STENCIL8:i.DEPTH_COMPONENT24,Me=z.stencil?Bs:Ta,$e=z.stencil?$o:qi);const Ze={colorFormat:i.RGBA8,depthFormat:at,scaleFactor:u};x=this.getBinding(),g=x.createProjectionLayer(Ze),l.updateRenderState({layers:[g]}),e.setPixelRatio(1),e.setSize(g.textureWidth,g.textureHeight,!1),C=new Wi(g.textureWidth,g.textureHeight,{format:Di,type:xi,depthTexture:new Vr(g.textureWidth,g.textureHeight,$e,void 0,void 0,void 0,void 0,void 0,void 0,Me),stencilBuffer:z.stencil,colorSpace:e.outputColorSpace,samples:z.antialias?4:0,resolveDepthBuffer:g.ignoreDepthValues===!1,resolveStencilBuffer:g.ignoreDepthValues===!1})}else{const Me={antialias:z.antialias,alpha:!0,depth:z.depth,stencil:z.stencil,framebufferScaleFactor:u};E=new XRWebGLLayer(l,i,Me),l.updateRenderState({baseLayer:E}),e.setPixelRatio(1),e.setSize(E.framebufferWidth,E.framebufferHeight,!1),C=new Wi(E.framebufferWidth,E.framebufferHeight,{format:Di,type:xi,colorSpace:e.outputColorSpace,stencilBuffer:z.stencil,resolveDepthBuffer:E.ignoreDepthValues===!1,resolveStencilBuffer:E.ignoreDepthValues===!1})}C.isXRRenderTarget=!0,this.setFoveation(m),p=null,d=await l.requestReferenceSpace(h),nt.setContext(l),nt.start(),r.isPresenting=!0,r.dispatchEvent({type:"sessionstart"})}},this.getEnvironmentBlendMode=function(){if(l!==null)return l.environmentBlendMode},this.getDepthTexture=function(){return y.getDepthTexture()};function I(ue){for(let Ae=0;Ae<ue.removed.length;Ae++){const Me=ue.removed[Ae],$e=U.indexOf(Me);$e>=0&&(U[$e]=null,O[$e].disconnect(Me))}for(let Ae=0;Ae<ue.added.length;Ae++){const Me=ue.added[Ae];let $e=U.indexOf(Me);if($e===-1){for(let Ze=0;Ze<O.length;Ze++)if(Ze>=U.length){U.push(Me),$e=Ze;break}else if(U[Ze]===null){U[Ze]=Me,$e=Ze;break}if($e===-1)break}const at=O[$e];at&&at.connect(Me)}}const $=new ae,se=new ae;function pe(ue,Ae,Me){$.setFromMatrixPosition(Ae.matrixWorld),se.setFromMatrixPosition(Me.matrixWorld);const $e=$.distanceTo(se),at=Ae.projectionMatrix.elements,Ze=Me.projectionMatrix.elements,Yt=at[14]/(at[10]-1),ut=at[14]/(at[10]+1),xt=(at[9]+1)/at[5],St=(at[9]-1)/at[5],ft=(at[8]-1)/at[0],en=(Ze[8]+1)/Ze[0],tn=Yt*ft,nn=Yt*en,ln=$e/(-ft+en),qt=ln*-ft;if(Ae.matrixWorld.decompose(ue.position,ue.quaternion,ue.scale),ue.translateX(qt),ue.translateZ(ln),ue.matrixWorld.compose(ue.position,ue.quaternion,ue.scale),ue.matrixWorldInverse.copy(ue.matrixWorld).invert(),at[10]===-1)ue.projectionMatrix.copy(Ae.projectionMatrix),ue.projectionMatrixInverse.copy(Ae.projectionMatrixInverse);else{const an=Yt+ln,X=ut+ln,Ht=tn-qt,wt=nn+($e-qt),w=xt*ut/X*an,M=St*ut/X*an;ue.projectionMatrix.makePerspective(Ht,wt,w,M,an,X),ue.projectionMatrixInverse.copy(ue.projectionMatrix).invert()}}function xe(ue,Ae){Ae===null?ue.matrixWorld.copy(ue.matrix):ue.matrixWorld.multiplyMatrices(Ae.matrixWorld,ue.matrix),ue.matrixWorldInverse.copy(ue.matrixWorld).invert()}this.updateCamera=function(ue){if(l===null)return;let Ae=ue.near,Me=ue.far;y.texture!==null&&(y.depthNear>0&&(Ae=y.depthNear),y.depthFar>0&&(Me=y.depthFar)),Z.near=W.near=L.near=Ae,Z.far=W.far=L.far=Me,(de!==Z.near||he!==Z.far)&&(l.updateRenderState({depthNear:Z.near,depthFar:Z.far}),de=Z.near,he=Z.far),Z.layers.mask=ue.layers.mask|6,L.layers.mask=Z.layers.mask&-5,W.layers.mask=Z.layers.mask&-3;const $e=ue.parent,at=Z.cameras;xe(Z,$e);for(let Ze=0;Ze<at.length;Ze++)xe(at[Ze],$e);at.length===2?pe(Z,L,W):Z.projectionMatrix.copy(L.projectionMatrix),Se(ue,Z,$e)};function Se(ue,Ae,Me){Me===null?ue.matrix.copy(Ae.matrixWorld):(ue.matrix.copy(Me.matrixWorld),ue.matrix.invert(),ue.matrix.multiply(Ae.matrixWorld)),ue.matrix.decompose(ue.position,ue.quaternion,ue.scale),ue.updateMatrixWorld(!0),ue.projectionMatrix.copy(Ae.projectionMatrix),ue.projectionMatrixInverse.copy(Ae.projectionMatrixInverse),ue.isPerspectiveCamera&&(ue.fov=jo*2*Math.atan(1/ue.projectionMatrix.elements[5]),ue.zoom=1)}this.getCamera=function(){return Z},this.getFoveation=function(){if(!(g===null&&E===null))return m},this.setFoveation=function(ue){m=ue,g!==null&&(g.fixedFoveation=ue),E!==null&&E.fixedFoveation!==void 0&&(E.fixedFoveation=ue)},this.hasDepthSensing=function(){return y.texture!==null},this.getDepthSensingMesh=function(){return y.getMesh(Z)},this.getCameraTexture=function(ue){return S[ue]};let Te=null;function tt(ue,Ae){if(v=Ae.getViewerPose(p||d),T=Ae,v!==null){const Me=v.views;E!==null&&(e.setRenderTargetFramebuffer(C,E.framebuffer),e.setRenderTarget(C));let $e=!1;Me.length!==Z.cameras.length&&(Z.cameras.length=0,$e=!0);for(let ut=0;ut<Me.length;ut++){const xt=Me[ut];let St=null;if(E!==null)St=E.getViewport(xt);else{const en=x.getViewSubImage(g,xt);St=en.viewport,ut===0&&(e.setRenderTargetTextures(C,en.colorTexture,en.depthStencilTexture),e.setRenderTarget(C))}let ft=V[ut];ft===void 0&&(ft=new vi,ft.layers.enable(ut),ft.viewport=new on,V[ut]=ft),ft.matrix.fromArray(xt.transform.matrix),ft.matrix.decompose(ft.position,ft.quaternion,ft.scale),ft.projectionMatrix.fromArray(xt.projectionMatrix),ft.projectionMatrixInverse.copy(ft.projectionMatrix).invert(),ft.viewport.set(St.x,St.y,St.width,St.height),ut===0&&(Z.matrix.copy(ft.matrix),Z.matrix.decompose(Z.position,Z.quaternion,Z.scale)),$e===!0&&Z.cameras.push(ft)}const at=l.enabledFeatures;if(at&&at.includes("depth-sensing")&&l.depthUsage=="gpu-optimized"&&D){x=r.getBinding();const ut=x.getDepthInformation(Me[0]);ut&&ut.isValid&&ut.texture&&y.init(ut,l.renderState)}if(at&&at.includes("camera-access")&&D){e.state.unbindTexture(),x=r.getBinding();for(let ut=0;ut<Me.length;ut++){const xt=Me[ut].camera;if(xt){let St=S[xt];St||(St=new Cv,S[xt]=St);const ft=x.getCameraImage(xt);St.sourceTexture=ft}}}}for(let Me=0;Me<O.length;Me++){const $e=U[Me],at=O[Me];$e!==null&&at!==void 0&&at.update($e,Ae,p||d)}Te&&Te(ue,Ae),Ae.detectedPlanes&&r.dispatchEvent({type:"planesdetected",data:Ae}),T=null}const nt=new Lv;nt.setAnimationLoop(tt),this.setAnimationLoop=function(ue){Te=ue},this.dispose=function(){}}}const MR=new fn,Iv=new st;Iv.set(-1,0,0,0,1,0,0,0,1);function yR(s,e){function i(y,S){y.matrixAutoUpdate===!0&&y.updateMatrix(),S.value.copy(y.matrix)}function r(y,S){S.color.getRGB(y.fogColor.value,wv(s)),S.isFog?(y.fogNear.value=S.near,y.fogFar.value=S.far):S.isFogExp2&&(y.fogDensity.value=S.density)}function l(y,S,z,F,C){S.isNodeMaterial?S.uniformsNeedUpdate=!1:S.isMeshBasicMaterial?u(y,S):S.isMeshLambertMaterial?(u(y,S),S.envMap&&(y.envMapIntensity.value=S.envMapIntensity)):S.isMeshToonMaterial?(u(y,S),x(y,S)):S.isMeshPhongMaterial?(u(y,S),v(y,S),S.envMap&&(y.envMapIntensity.value=S.envMapIntensity)):S.isMeshStandardMaterial?(u(y,S),g(y,S),S.isMeshPhysicalMaterial&&E(y,S,C)):S.isMeshMatcapMaterial?(u(y,S),T(y,S)):S.isMeshDepthMaterial?u(y,S):S.isMeshDistanceMaterial?(u(y,S),D(y,S)):S.isMeshNormalMaterial?u(y,S):S.isLineBasicMaterial?(d(y,S),S.isLineDashedMaterial&&h(y,S)):S.isPointsMaterial?m(y,S,z,F):S.isSpriteMaterial?p(y,S):S.isShadowMaterial?(y.color.value.copy(S.color),y.opacity.value=S.opacity):S.isShaderMaterial&&(S.uniformsNeedUpdate=!1)}function u(y,S){y.opacity.value=S.opacity,S.color&&y.diffuse.value.copy(S.color),S.emissive&&y.emissive.value.copy(S.emissive).multiplyScalar(S.emissiveIntensity),S.map&&(y.map.value=S.map,i(S.map,y.mapTransform)),S.alphaMap&&(y.alphaMap.value=S.alphaMap,i(S.alphaMap,y.alphaMapTransform)),S.bumpMap&&(y.bumpMap.value=S.bumpMap,i(S.bumpMap,y.bumpMapTransform),y.bumpScale.value=S.bumpScale,S.side===Yn&&(y.bumpScale.value*=-1)),S.normalMap&&(y.normalMap.value=S.normalMap,i(S.normalMap,y.normalMapTransform),y.normalScale.value.copy(S.normalScale),S.side===Yn&&y.normalScale.value.negate()),S.displacementMap&&(y.displacementMap.value=S.displacementMap,i(S.displacementMap,y.displacementMapTransform),y.displacementScale.value=S.displacementScale,y.displacementBias.value=S.displacementBias),S.emissiveMap&&(y.emissiveMap.value=S.emissiveMap,i(S.emissiveMap,y.emissiveMapTransform)),S.specularMap&&(y.specularMap.value=S.specularMap,i(S.specularMap,y.specularMapTransform)),S.alphaTest>0&&(y.alphaTest.value=S.alphaTest);const z=e.get(S),F=z.envMap,C=z.envMapRotation;F&&(y.envMap.value=F,y.envMapRotation.value.setFromMatrix4(MR.makeRotationFromEuler(C)).transpose(),F.isCubeTexture&&F.isRenderTargetTexture===!1&&y.envMapRotation.value.premultiply(Iv),y.reflectivity.value=S.reflectivity,y.ior.value=S.ior,y.refractionRatio.value=S.refractionRatio),S.lightMap&&(y.lightMap.value=S.lightMap,y.lightMapIntensity.value=S.lightMapIntensity,i(S.lightMap,y.lightMapTransform)),S.aoMap&&(y.aoMap.value=S.aoMap,y.aoMapIntensity.value=S.aoMapIntensity,i(S.aoMap,y.aoMapTransform))}function d(y,S){y.diffuse.value.copy(S.color),y.opacity.value=S.opacity,S.map&&(y.map.value=S.map,i(S.map,y.mapTransform))}function h(y,S){y.dashSize.value=S.dashSize,y.totalSize.value=S.dashSize+S.gapSize,y.scale.value=S.scale}function m(y,S,z,F){y.diffuse.value.copy(S.color),y.opacity.value=S.opacity,y.size.value=S.size*z,y.scale.value=F*.5,S.map&&(y.map.value=S.map,i(S.map,y.uvTransform)),S.alphaMap&&(y.alphaMap.value=S.alphaMap,i(S.alphaMap,y.alphaMapTransform)),S.alphaTest>0&&(y.alphaTest.value=S.alphaTest)}function p(y,S){y.diffuse.value.copy(S.color),y.opacity.value=S.opacity,y.rotation.value=S.rotation,S.map&&(y.map.value=S.map,i(S.map,y.mapTransform)),S.alphaMap&&(y.alphaMap.value=S.alphaMap,i(S.alphaMap,y.alphaMapTransform)),S.alphaTest>0&&(y.alphaTest.value=S.alphaTest)}function v(y,S){y.specular.value.copy(S.specular),y.shininess.value=Math.max(S.shininess,1e-4)}function x(y,S){S.gradientMap&&(y.gradientMap.value=S.gradientMap)}function g(y,S){y.metalness.value=S.metalness,S.metalnessMap&&(y.metalnessMap.value=S.metalnessMap,i(S.metalnessMap,y.metalnessMapTransform)),y.roughness.value=S.roughness,S.roughnessMap&&(y.roughnessMap.value=S.roughnessMap,i(S.roughnessMap,y.roughnessMapTransform)),S.envMap&&(y.envMapIntensity.value=S.envMapIntensity)}function E(y,S,z){y.ior.value=S.ior,S.sheen>0&&(y.sheenColor.value.copy(S.sheenColor).multiplyScalar(S.sheen),y.sheenRoughness.value=S.sheenRoughness,S.sheenColorMap&&(y.sheenColorMap.value=S.sheenColorMap,i(S.sheenColorMap,y.sheenColorMapTransform)),S.sheenRoughnessMap&&(y.sheenRoughnessMap.value=S.sheenRoughnessMap,i(S.sheenRoughnessMap,y.sheenRoughnessMapTransform))),S.clearcoat>0&&(y.clearcoat.value=S.clearcoat,y.clearcoatRoughness.value=S.clearcoatRoughness,S.clearcoatMap&&(y.clearcoatMap.value=S.clearcoatMap,i(S.clearcoatMap,y.clearcoatMapTransform)),S.clearcoatRoughnessMap&&(y.clearcoatRoughnessMap.value=S.clearcoatRoughnessMap,i(S.clearcoatRoughnessMap,y.clearcoatRoughnessMapTransform)),S.clearcoatNormalMap&&(y.clearcoatNormalMap.value=S.clearcoatNormalMap,i(S.clearcoatNormalMap,y.clearcoatNormalMapTransform),y.clearcoatNormalScale.value.copy(S.clearcoatNormalScale),S.side===Yn&&y.clearcoatNormalScale.value.negate())),S.dispersion>0&&(y.dispersion.value=S.dispersion),S.iridescence>0&&(y.iridescence.value=S.iridescence,y.iridescenceIOR.value=S.iridescenceIOR,y.iridescenceThicknessMinimum.value=S.iridescenceThicknessRange[0],y.iridescenceThicknessMaximum.value=S.iridescenceThicknessRange[1],S.iridescenceMap&&(y.iridescenceMap.value=S.iridescenceMap,i(S.iridescenceMap,y.iridescenceMapTransform)),S.iridescenceThicknessMap&&(y.iridescenceThicknessMap.value=S.iridescenceThicknessMap,i(S.iridescenceThicknessMap,y.iridescenceThicknessMapTransform))),S.transmission>0&&(y.transmission.value=S.transmission,y.transmissionSamplerMap.value=z.texture,y.transmissionSamplerSize.value.set(z.width,z.height),S.transmissionMap&&(y.transmissionMap.value=S.transmissionMap,i(S.transmissionMap,y.transmissionMapTransform)),y.thickness.value=S.thickness,S.thicknessMap&&(y.thicknessMap.value=S.thicknessMap,i(S.thicknessMap,y.thicknessMapTransform)),y.attenuationDistance.value=S.attenuationDistance,y.attenuationColor.value.copy(S.attenuationColor)),S.anisotropy>0&&(y.anisotropyVector.value.set(S.anisotropy*Math.cos(S.anisotropyRotation),S.anisotropy*Math.sin(S.anisotropyRotation)),S.anisotropyMap&&(y.anisotropyMap.value=S.anisotropyMap,i(S.anisotropyMap,y.anisotropyMapTransform))),y.specularIntensity.value=S.specularIntensity,y.specularColor.value.copy(S.specularColor),S.specularColorMap&&(y.specularColorMap.value=S.specularColorMap,i(S.specularColorMap,y.specularColorMapTransform)),S.specularIntensityMap&&(y.specularIntensityMap.value=S.specularIntensityMap,i(S.specularIntensityMap,y.specularIntensityMapTransform))}function T(y,S){S.matcap&&(y.matcap.value=S.matcap)}function D(y,S){const z=e.get(S).light;y.referencePosition.value.setFromMatrixPosition(z.matrixWorld),y.nearDistance.value=z.shadow.camera.near,y.farDistance.value=z.shadow.camera.far}return{refreshFogUniforms:r,refreshMaterialUniforms:l}}function ER(s,e,i,r){let l={},u={},d=[];const h=s.getParameter(s.MAX_UNIFORM_BUFFER_BINDINGS);function m(C,O){const U=O.program;r.uniformBlockBinding(C,U)}function p(C,O){let U=l[C.id];U===void 0&&(y(C),U=v(C),l[C.id]=U,C.addEventListener("dispose",z));const B=O.program;r.updateUBOMapping(C,B);const b=e.render.frame;u[C.id]!==b&&(g(C),u[C.id]=b)}function v(C){const O=x();C.__bindingPointIndex=O;const U=s.createBuffer(),B=C.__size,b=C.usage;return s.bindBuffer(s.UNIFORM_BUFFER,U),s.bufferData(s.UNIFORM_BUFFER,B,b),s.bindBuffer(s.UNIFORM_BUFFER,null),s.bindBufferBase(s.UNIFORM_BUFFER,O,U),U}function x(){for(let C=0;C<h;C++)if(d.indexOf(C)===-1)return d.push(C),C;return bt("WebGLRenderer: Maximum number of simultaneously usable uniforms groups reached."),0}function g(C){const O=l[C.id],U=C.uniforms,B=C.__cache;s.bindBuffer(s.UNIFORM_BUFFER,O);for(let b=0,L=U.length;b<L;b++){const W=U[b];if(Array.isArray(W))for(let V=0,Z=W.length;V<Z;V++)E(W[V],b,V,B);else E(W,b,0,B)}s.bindBuffer(s.UNIFORM_BUFFER,null)}function E(C,O,U,B){if(D(C,O,U,B)===!0){const b=C.__offset,L=C.value;if(Array.isArray(L)){let W=0;for(let V=0;V<L.length;V++){const Z=L[V],de=S(Z);T(Z,C.__data,W),typeof Z!="number"&&typeof Z!="boolean"&&!Z.isMatrix3&&!ArrayBuffer.isView(Z)&&(W+=de.storage/Float32Array.BYTES_PER_ELEMENT)}}else T(L,C.__data,0);s.bufferSubData(s.UNIFORM_BUFFER,b,C.__data)}}function T(C,O,U){typeof C=="number"||typeof C=="boolean"?O[0]=C:C.isMatrix3?(O[0]=C.elements[0],O[1]=C.elements[1],O[2]=C.elements[2],O[3]=0,O[4]=C.elements[3],O[5]=C.elements[4],O[6]=C.elements[5],O[7]=0,O[8]=C.elements[6],O[9]=C.elements[7],O[10]=C.elements[8],O[11]=0):ArrayBuffer.isView(C)?O.set(new C.constructor(C.buffer,C.byteOffset,O.length)):C.toArray(O,U)}function D(C,O,U,B){const b=C.value,L=O+"_"+U;if(B[L]===void 0)return typeof b=="number"||typeof b=="boolean"?B[L]=b:ArrayBuffer.isView(b)?B[L]=b.slice():B[L]=b.clone(),!0;{const W=B[L];if(typeof b=="number"||typeof b=="boolean"){if(W!==b)return B[L]=b,!0}else{if(ArrayBuffer.isView(b))return!0;if(W.equals(b)===!1)return W.copy(b),!0}}return!1}function y(C){const O=C.uniforms;let U=0;const B=16;for(let L=0,W=O.length;L<W;L++){const V=Array.isArray(O[L])?O[L]:[O[L]];for(let Z=0,de=V.length;Z<de;Z++){const he=V[Z],K=Array.isArray(he.value)?he.value:[he.value];for(let N=0,I=K.length;N<I;N++){const $=K[N],se=S($),pe=U%B,xe=pe%se.boundary,Se=pe+xe;U+=xe,Se!==0&&B-Se<se.storage&&(U+=B-Se),he.__data=new Float32Array(se.storage/Float32Array.BYTES_PER_ELEMENT),he.__offset=U,U+=se.storage}}}const b=U%B;return b>0&&(U+=B-b),C.__size=U,C.__cache={},this}function S(C){const O={boundary:0,storage:0};return typeof C=="number"||typeof C=="boolean"?(O.boundary=4,O.storage=4):C.isVector2?(O.boundary=8,O.storage=8):C.isVector3||C.isColor?(O.boundary=16,O.storage=12):C.isVector4?(O.boundary=16,O.storage=16):C.isMatrix3?(O.boundary=48,O.storage=48):C.isMatrix4?(O.boundary=64,O.storage=64):C.isTexture?et("WebGLRenderer: Texture samplers can not be part of an uniforms group."):ArrayBuffer.isView(C)?(O.boundary=16,O.storage=C.byteLength):et("WebGLRenderer: Unsupported uniform value type.",C),O}function z(C){const O=C.target;O.removeEventListener("dispose",z);const U=d.indexOf(O.__bindingPointIndex);d.splice(U,1),s.deleteBuffer(l[O.id]),delete l[O.id],delete u[O.id]}function F(){for(const C in l)s.deleteBuffer(l[C]);d=[],l={},u={}}return{bind:m,update:p,dispose:F}}const bR=new Uint16Array([12469,15057,12620,14925,13266,14620,13807,14376,14323,13990,14545,13625,14713,13328,14840,12882,14931,12528,14996,12233,15039,11829,15066,11525,15080,11295,15085,10976,15082,10705,15073,10495,13880,14564,13898,14542,13977,14430,14158,14124,14393,13732,14556,13410,14702,12996,14814,12596,14891,12291,14937,11834,14957,11489,14958,11194,14943,10803,14921,10506,14893,10278,14858,9960,14484,14039,14487,14025,14499,13941,14524,13740,14574,13468,14654,13106,14743,12678,14818,12344,14867,11893,14889,11509,14893,11180,14881,10751,14852,10428,14812,10128,14765,9754,14712,9466,14764,13480,14764,13475,14766,13440,14766,13347,14769,13070,14786,12713,14816,12387,14844,11957,14860,11549,14868,11215,14855,10751,14825,10403,14782,10044,14729,9651,14666,9352,14599,9029,14967,12835,14966,12831,14963,12804,14954,12723,14936,12564,14917,12347,14900,11958,14886,11569,14878,11247,14859,10765,14828,10401,14784,10011,14727,9600,14660,9289,14586,8893,14508,8533,15111,12234,15110,12234,15104,12216,15092,12156,15067,12010,15028,11776,14981,11500,14942,11205,14902,10752,14861,10393,14812,9991,14752,9570,14682,9252,14603,8808,14519,8445,14431,8145,15209,11449,15208,11451,15202,11451,15190,11438,15163,11384,15117,11274,15055,10979,14994,10648,14932,10343,14871,9936,14803,9532,14729,9218,14645,8742,14556,8381,14461,8020,14365,7603,15273,10603,15272,10607,15267,10619,15256,10631,15231,10614,15182,10535,15118,10389,15042,10167,14963,9787,14883,9447,14800,9115,14710,8665,14615,8318,14514,7911,14411,7507,14279,7198,15314,9675,15313,9683,15309,9712,15298,9759,15277,9797,15229,9773,15166,9668,15084,9487,14995,9274,14898,8910,14800,8539,14697,8234,14590,7790,14479,7409,14367,7067,14178,6621,15337,8619,15337,8631,15333,8677,15325,8769,15305,8871,15264,8940,15202,8909,15119,8775,15022,8565,14916,8328,14804,8009,14688,7614,14569,7287,14448,6888,14321,6483,14088,6171,15350,7402,15350,7419,15347,7480,15340,7613,15322,7804,15287,7973,15229,8057,15148,8012,15046,7846,14933,7611,14810,7357,14682,7069,14552,6656,14421,6316,14251,5948,14007,5528,15356,5942,15356,5977,15353,6119,15348,6294,15332,6551,15302,6824,15249,7044,15171,7122,15070,7050,14949,6861,14818,6611,14679,6349,14538,6067,14398,5651,14189,5311,13935,4958,15359,4123,15359,4153,15356,4296,15353,4646,15338,5160,15311,5508,15263,5829,15188,6042,15088,6094,14966,6001,14826,5796,14678,5543,14527,5287,14377,4985,14133,4586,13869,4257,15360,1563,15360,1642,15358,2076,15354,2636,15341,3350,15317,4019,15273,4429,15203,4732,15105,4911,14981,4932,14836,4818,14679,4621,14517,4386,14359,4156,14083,3795,13808,3437,15360,122,15360,137,15358,285,15355,636,15344,1274,15322,2177,15281,2765,15215,3223,15120,3451,14995,3569,14846,3567,14681,3466,14511,3305,14344,3121,14037,2800,13753,2467,15360,0,15360,1,15359,21,15355,89,15346,253,15325,479,15287,796,15225,1148,15133,1492,15008,1749,14856,1882,14685,1886,14506,1783,14324,1608,13996,1398,13702,1183]);let Hi=null;function TR(){return Hi===null&&(Hi=new mE(bR,16,16,Hs,ba),Hi.name="DFG_LUT",Hi.minFilter=On,Hi.magFilter=On,Hi.wrapS=Sa,Hi.wrapT=Sa,Hi.generateMipmaps=!1,Hi.needsUpdate=!0),Hi}class AR{constructor(e={}){const{canvas:i=Uy(),context:r=null,depth:l=!0,stencil:u=!1,alpha:d=!1,antialias:h=!1,premultipliedAlpha:m=!0,preserveDrawingBuffer:p=!1,powerPreference:v="default",failIfMajorPerformanceCaveat:x=!1,reversedDepthBuffer:g=!1,outputBufferType:E=xi}=e;this.isWebGLRenderer=!0;let T;if(r!==null){if(typeof WebGLRenderingContext<"u"&&r instanceof WebGLRenderingContext)throw new Error("THREE.WebGLRenderer: WebGL 1 is not supported since r163.");T=r.getContextAttributes().alpha}else T=d;const D=E,y=new Set([Xh,kh,Vh]),S=new Set([xi,qi,Jo,$o,Hh,Gh]),z=new Uint32Array(4),F=new Int32Array(4),C=new ae;let O=null,U=null;const B=[],b=[];let L=null;this.domElement=i,this.debug={checkShaderErrors:!0,onShaderError:null},this.autoClear=!0,this.autoClearColor=!0,this.autoClearDepth=!0,this.autoClearStencil=!0,this.sortObjects=!0,this.clippingPlanes=[],this.localClippingEnabled=!1,this.toneMapping=Xi,this.toneMappingExposure=1,this.transmissionResolutionScale=1;const W=this;let V=!1,Z=null,de=null,he=null,K=null;this._outputColorSpace=_i;let N=0,I=0,$=null,se=-1,pe=null;const xe=new on,Se=new on;let Te=null;const tt=new Et(0);let nt=0,ue=i.width,Ae=i.height,Me=1,$e=null,at=null;const Ze=new on(0,0,ue,Ae),Yt=new on(0,0,ue,Ae);let ut=!1;const xt=new Tv;let St=!1,ft=!1;const en=new fn,tn=new ae,nn=new on,ln={background:null,fog:null,environment:null,overrideMaterial:null,isScene:!0};let qt=!1;function an(){return $===null?Me:1}let X=r;function Ht(A,k){return i.getContext(A,k)}try{const A={alpha:!0,depth:l,stencil:u,antialias:h,premultipliedAlpha:m,preserveDrawingBuffer:p,powerPreference:v,failIfMajorPerformanceCaveat:x};if("setAttribute"in i&&i.setAttribute("data-engine",`three.js r${Bh}`),i.addEventListener("webglcontextlost",Qt,!1),i.addEventListener("webglcontextrestored",Nt,!1),i.addEventListener("webglcontextcreationerror",Kn,!1),X===null){const k="webgl2";if(X=Ht(k,A),X===null)throw Ht(k)?new Error("THREE.WebGLRenderer: Error creating WebGL context with your selected attributes."):new Error("THREE.WebGLRenderer: Error creating WebGL context.")}}catch(A){throw bt("WebGLRenderer: "+A.message),A}let wt,w,M,Y,te,le,ye,Ce,oe,ce,be,Pe,Ue,we,Ye,Ke,it,G,Ee,me,Re,Oe,ve;function ke(){wt=new T1(X),wt.init(),Re=new gR(X,wt),w=new _1(X,wt,e,Re),M=new pR(X,wt),w.reversedDepthBuffer&&g&&M.buffers.depth.setReversed(!0),de=X.createFramebuffer(),he=X.createFramebuffer(),K=X.createFramebuffer(),Y=new C1(X),te=new eR,le=new mR(X,wt,M,te,w,Re,Y),ye=new b1(W),Ce=new LE(X),Oe=new m1(X,Ce),oe=new A1(X,Ce,Y,Oe),ce=new D1(X,oe,Ce,Oe,Y),G=new w1(X,w,le),Ye=new v1(te),be=new jA(W,ye,wt,w,Oe,Ye),Pe=new yR(W,te),Ue=new nR,we=new lR(wt),it=new p1(W,ye,M,ce,T,m),Ke=new hR(W,ce,w),ve=new ER(X,Y,w,M),Ee=new g1(X,wt,Y),me=new R1(X,wt,Y),Y.programs=be.programs,W.capabilities=w,W.extensions=wt,W.properties=te,W.renderLists=Ue,W.shadowMap=Ke,W.state=M,W.info=Y}ke(),D!==xi&&(L=new L1(D,i.width,i.height,h,l,u));const Ie=new SR(W,X);this.xr=Ie,this.getContext=function(){return X},this.getContextAttributes=function(){return X.getContextAttributes()},this.forceContextLoss=function(){const A=wt.get("WEBGL_lose_context");A&&A.loseContext()},this.forceContextRestore=function(){const A=wt.get("WEBGL_lose_context");A&&A.restoreContext()},this.getPixelRatio=function(){return Me},this.setPixelRatio=function(A){A!==void 0&&(Me=A,this.setSize(ue,Ae,!1))},this.getSize=function(A){return A.set(ue,Ae)},this.setSize=function(A,k,ne=!0){if(Ie.isPresenting){et("WebGLRenderer: Can't change size while VR device is presenting.");return}ue=A,Ae=k,i.width=Math.floor(A*Me),i.height=Math.floor(k*Me),ne===!0&&(i.style.width=A+"px",i.style.height=k+"px"),L!==null&&L.setSize(i.width,i.height),this.setViewport(0,0,A,k)},this.getDrawingBufferSize=function(A){return A.set(ue*Me,Ae*Me).floor()},this.setDrawingBufferSize=function(A,k,ne){ue=A,Ae=k,Me=ne,i.width=Math.floor(A*ne),i.height=Math.floor(k*ne),this.setViewport(0,0,A,k)},this.setEffects=function(A){if(D===xi){bt("WebGLRenderer: setEffects() requires outputBufferType set to HalfFloatType or FloatType.");return}if(A){for(let k=0;k<A.length;k++)if(A[k].isOutputPass===!0){et("WebGLRenderer: OutputPass is not needed in setEffects(). Tone mapping and color space conversion are applied automatically.");break}}L.setEffects(A||[])},this.getCurrentViewport=function(A){return A.copy(xe)},this.getViewport=function(A){return A.copy(Ze)},this.setViewport=function(A,k,ne,j){A.isVector4?Ze.set(A.x,A.y,A.z,A.w):Ze.set(A,k,ne,j),M.viewport(xe.copy(Ze).multiplyScalar(Me).round())},this.getScissor=function(A){return A.copy(Yt)},this.setScissor=function(A,k,ne,j){A.isVector4?Yt.set(A.x,A.y,A.z,A.w):Yt.set(A,k,ne,j),M.scissor(Se.copy(Yt).multiplyScalar(Me).round())},this.getScissorTest=function(){return ut},this.setScissorTest=function(A){M.setScissorTest(ut=A)},this.setOpaqueSort=function(A){$e=A},this.setTransparentSort=function(A){at=A},this.getClearColor=function(A){return A.copy(it.getClearColor())},this.setClearColor=function(){it.setClearColor(...arguments)},this.getClearAlpha=function(){return it.getClearAlpha()},this.setClearAlpha=function(){it.setClearAlpha(...arguments)},this.clear=function(A=!0,k=!0,ne=!0){let j=0;if(A){let ee=!1;if($!==null){const Le=$.texture.format;ee=y.has(Le)}if(ee){const Le=$.texture.type,Fe=S.has(Le),De=it.getClearColor(),Ge=it.getClearAlpha(),He=De.r,Qe=De.g,rt=De.b;Fe?(z[0]=He,z[1]=Qe,z[2]=rt,z[3]=Ge,X.clearBufferuiv(X.COLOR,0,z)):(F[0]=He,F[1]=Qe,F[2]=rt,F[3]=Ge,X.clearBufferiv(X.COLOR,0,F))}else j|=X.COLOR_BUFFER_BIT}k&&(j|=X.DEPTH_BUFFER_BIT,this.state.buffers.depth.setMask(!0)),ne&&(j|=X.STENCIL_BUFFER_BIT,this.state.buffers.stencil.setMask(4294967295)),j!==0&&X.clear(j)},this.clearColor=function(){this.clear(!0,!1,!1)},this.clearDepth=function(){this.clear(!1,!0,!1)},this.clearStencil=function(){this.clear(!1,!1,!0)},this.setNodesHandler=function(A){A.setRenderer(this),Z=A},this.dispose=function(){i.removeEventListener("webglcontextlost",Qt,!1),i.removeEventListener("webglcontextrestored",Nt,!1),i.removeEventListener("webglcontextcreationerror",Kn,!1),it.dispose(),Ue.dispose(),we.dispose(),te.dispose(),ye.dispose(),ce.dispose(),Oe.dispose(),ve.dispose(),be.dispose(),Ie.dispose(),Ie.removeEventListener("sessionstart",dn),Ie.removeEventListener("sessionend",Tn),Hn.stop()};function Qt(A){A.preventDefault(),e_("WebGLRenderer: Context Lost."),V=!0}function Nt(){e_("WebGLRenderer: Context Restored."),V=!1;const A=Y.autoReset,k=Ke.enabled,ne=Ke.autoUpdate,j=Ke.needsUpdate,ee=Ke.type;ke(),Y.autoReset=A,Ke.enabled=k,Ke.autoUpdate=ne,Ke.needsUpdate=j,Ke.type=ee}function Kn(A){bt("WebGLRenderer: A WebGL context could not be created. Reason: ",A.statusMessage)}function Qn(A){const k=A.target;k.removeEventListener("dispose",Qn),Yr(k)}function Yr(A){Zr(A),te.remove(A)}function Zr(A){const k=te.get(A).programs;k!==void 0&&(k.forEach(function(ne){be.releaseProgram(ne)}),A.isShaderMaterial&&be.releaseShaderCache(A))}this.renderBufferDirect=function(A,k,ne,j,ee,Le){k===null&&(k=ln);const Fe=ee.isMesh&&ee.matrixWorld.determinantAffine()<0,De=Ca(A,k,ne,j,ee);M.setMaterial(j,Fe);let Ge=ne.index,He=1;if(j.wireframe===!0){if(Ge=oe.getWireframeAttribute(ne),Ge===void 0)return;He=2}const Qe=ne.drawRange,rt=ne.attributes.position;let qe=Qe.start*He,Tt=(Qe.start+Qe.count)*He;Le!==null&&(qe=Math.max(qe,Le.start*He),Tt=Math.min(Tt,(Le.start+Le.count)*He)),Ge!==null?(qe=Math.max(qe,0),Tt=Math.min(Tt,Ge.count)):rt!=null&&(qe=Math.max(qe,0),Tt=Math.min(Tt,rt.count));const Jt=Tt-qe;if(Jt<0||Jt===1/0)return;Oe.setup(ee,j,De,ne,Ge);let Xt,Ot=Ee;if(Ge!==null&&(Xt=Ce.get(Ge),Ot=me,Ot.setIndex(Xt)),ee.isMesh)j.wireframe===!0?(M.setLineWidth(j.wireframeLinewidth*an()),Ot.setMode(X.LINES)):Ot.setMode(X.TRIANGLES);else if(ee.isLine){let Pt=j.linewidth;Pt===void 0&&(Pt=1),M.setLineWidth(Pt*an()),ee.isLineSegments?Ot.setMode(X.LINES):ee.isLineLoop?Ot.setMode(X.LINE_LOOP):Ot.setMode(X.LINE_STRIP)}else ee.isPoints?Ot.setMode(X.POINTS):ee.isSprite&&Ot.setMode(X.TRIANGLES);if(ee.isBatchedMesh)if(wt.get("WEBGL_multi_draw"))Ot.renderMultiDraw(ee._multiDrawStarts,ee._multiDrawCounts,ee._multiDrawCount);else{const Pt=ee._multiDrawStarts,ze=ee._multiDrawCounts,Un=ee._multiDrawCount,dt=Ge?Ce.get(Ge).bytesPerElement:1,xn=te.get(j).currentProgram.getUniforms();for(let Jn=0;Jn<Un;Jn++)xn.setValue(X,"_gl_DrawID",Jn),Ot.render(Pt[Jn]/dt,ze[Jn])}else if(ee.isInstancedMesh)Ot.renderInstances(qe,Jt,ee.count);else if(ne.isInstancedBufferGeometry){const Pt=ne._maxInstanceCount!==void 0?ne._maxInstanceCount:1/0,ze=Math.min(ne.instanceCount,Pt);Ot.renderInstances(qe,Jt,ze)}else Ot.render(qe,Jt)};function Kr(A,k,ne){A.transparent===!0&&A.side===xa&&A.forceSinglePass===!1?(A.side=Yn,A.needsUpdate=!0,Ra(A,k,ne),A.side=ls,A.needsUpdate=!0,Ra(A,k,ne),A.side=xa):Ra(A,k,ne)}this.compile=function(A,k,ne=null){ne===null&&(ne=A),U=we.get(ne),U.init(k),b.push(U),ne.traverseVisible(function(ee){ee.isLight&&ee.layers.test(k.layers)&&(U.pushLight(ee),ee.castShadow&&U.pushShadow(ee))}),A!==ne&&A.traverseVisible(function(ee){ee.isLight&&ee.layers.test(k.layers)&&(U.pushLight(ee),ee.castShadow&&U.pushShadow(ee))}),U.setupLights();const j=new Set;return A.traverse(function(ee){if(!(ee.isMesh||ee.isPoints||ee.isLine||ee.isSprite))return;const Le=ee.material;if(Le)if(Array.isArray(Le))for(let Fe=0;Fe<Le.length;Fe++){const De=Le[Fe];Kr(De,ne,ee),j.add(De)}else Kr(Le,ne,ee),j.add(Le)}),U=b.pop(),j},this.compileAsync=function(A,k,ne=null){const j=this.compile(A,k,ne);return new Promise(ee=>{function Le(){if(j.forEach(function(Fe){te.get(Fe).currentProgram.isReady()&&j.delete(Fe)}),j.size===0){ee(A);return}setTimeout(Le,10)}wt.get("KHR_parallel_shader_compile")!==null?Le():setTimeout(Le,10)})};let ks=null;function Ni(A){ks&&ks(A)}function dn(){Hn.stop()}function Tn(){Hn.start()}const Hn=new Lv;Hn.setAnimationLoop(Ni),typeof self<"u"&&Hn.setContext(self),this.setAnimationLoop=function(A){ks=A,Ie.setAnimationLoop(A),A===null?Hn.stop():Hn.start()},Ie.addEventListener("sessionstart",dn),Ie.addEventListener("sessionend",Tn),this.render=function(A,k){if(k!==void 0&&k.isCamera!==!0){bt("WebGLRenderer.render: camera is not an instance of THREE.Camera.");return}if(V===!0)return;Z!==null&&Z.renderStart(A,k);const ne=Ie.enabled===!0&&Ie.isPresenting===!0,j=L!==null&&($===null||ne)&&L.begin(W,$);if(A.matrixWorldAutoUpdate===!0&&A.updateMatrixWorld(),k.parent===null&&k.matrixWorldAutoUpdate===!0&&k.updateMatrixWorld(),Ie.enabled===!0&&Ie.isPresenting===!0&&(L===null||L.isCompositing()===!1)&&(Ie.cameraAutoUpdate===!0&&Ie.updateCamera(k),k=Ie.getCamera()),A.isScene===!0&&A.onBeforeRender(W,A,k,$),U=we.get(A,b.length),U.init(k),U.state.textureUnits=le.getTextureUnits(),b.push(U),en.multiplyMatrices(k.projectionMatrix,k.matrixWorldInverse),xt.setFromProjectionMatrix(en,ki,k.reversedDepth),ft=this.localClippingEnabled,St=Ye.init(this.clippingPlanes,ft),O=Ue.get(A,B.length),O.init(),B.push(O),Ie.enabled===!0&&Ie.isPresenting===!0){const Fe=W.xr.getDepthSensingMesh();Fe!==null&&cs(Fe,k,-1/0,W.sortObjects)}cs(A,k,0,W.sortObjects),O.finish(),W.sortObjects===!0&&O.sort($e,at,k.reversedDepth),qt=Ie.enabled===!1||Ie.isPresenting===!1||Ie.hasDepthSensing()===!1,qt&&it.addToRenderList(O,A),this.info.render.frame++,this.info.autoReset===!0&&this.info.reset(),St===!0&&Ye.beginShadows();const ee=U.state.shadowsArray;if(Ke.render(ee,A,k),St===!0&&Ye.endShadows(),(j&&L.hasRenderPass())===!1){const Fe=O.opaque,De=O.transmissive;if(U.setupLights(),k.isArrayCamera){const Ge=k.cameras;if(De.length>0)for(let He=0,Qe=Ge.length;He<Qe;He++){const rt=Ge[He];sl(Fe,De,A,rt)}qt&&it.render(A);for(let He=0,Qe=Ge.length;He<Qe;He++){const rt=Ge[He];al(O,A,rt,rt.viewport)}}else De.length>0&&sl(Fe,De,A,k),qt&&it.render(A),al(O,A,k)}$!==null&&I===0&&(le.updateMultisampleRenderTarget($),le.updateRenderTargetMipmap($)),j&&L.end(W),A.isScene===!0&&A.onAfterRender(W,A,k),Oe.resetDefaultState(),se=-1,pe=null,b.pop(),b.length>0?(U=b[b.length-1],le.setTextureUnits(U.state.textureUnits),St===!0&&Ye.setGlobalState(W.clippingPlanes,U.state.camera)):U=null,B.pop(),B.length>0?O=B[B.length-1]:O=null,Z!==null&&Z.renderEnd()};function cs(A,k,ne,j){if(A.visible===!1)return;if(A.layers.test(k.layers)){if(A.isGroup)ne=A.renderOrder;else if(A.isLOD)A.autoUpdate===!0&&A.update(k);else if(A.isLightProbeGrid)U.pushLightProbeGrid(A);else if(A.isLight)U.pushLight(A),A.castShadow&&U.pushShadow(A);else if(A.isSprite){if(!A.frustumCulled||xt.intersectsSprite(A)){j&&nn.setFromMatrixPosition(A.matrixWorld).applyMatrix4(en);const Fe=ce.update(A),De=A.material;De.visible&&O.push(A,Fe,De,ne,nn.z,null)}}else if((A.isMesh||A.isLine||A.isPoints)&&(!A.frustumCulled||xt.intersectsObject(A))){const Fe=ce.update(A),De=A.material;if(j&&(A.boundingSphere!==void 0?(A.boundingSphere===null&&A.computeBoundingSphere(),nn.copy(A.boundingSphere.center)):(Fe.boundingSphere===null&&Fe.computeBoundingSphere(),nn.copy(Fe.boundingSphere.center)),nn.applyMatrix4(A.matrixWorld).applyMatrix4(en)),Array.isArray(De)){const Ge=Fe.groups;for(let He=0,Qe=Ge.length;He<Qe;He++){const rt=Ge[He],qe=De[rt.materialIndex];qe&&qe.visible&&O.push(A,Fe,qe,ne,nn.z,rt)}}else De.visible&&O.push(A,Fe,De,ne,nn.z,null)}}const Le=A.children;for(let Fe=0,De=Le.length;Fe<De;Fe++)cs(Le[Fe],k,ne,j)}function al(A,k,ne,j){const{opaque:ee,transmissive:Le,transparent:Fe}=A;U.setupLightsView(ne),St===!0&&Ye.setGlobalState(W.clippingPlanes,ne),j&&M.viewport(xe.copy(j)),ee.length>0&&us(ee,k,ne),Le.length>0&&us(Le,k,ne),Fe.length>0&&us(Fe,k,ne),M.buffers.depth.setTest(!0),M.buffers.depth.setMask(!0),M.buffers.color.setMask(!0),M.setPolygonOffset(!1)}function sl(A,k,ne,j){if((ne.isScene===!0?ne.overrideMaterial:null)!==null)return;if(U.state.transmissionRenderTarget[j.id]===void 0){const qe=wt.has("EXT_color_buffer_half_float")||wt.has("EXT_color_buffer_float");U.state.transmissionRenderTarget[j.id]=new Wi(1,1,{generateMipmaps:!0,type:qe?ba:xi,minFilter:Fs,samples:Math.max(4,w.samples),stencilBuffer:u,resolveDepthBuffer:!1,resolveStencilBuffer:!1,colorSpace:yt.workingColorSpace})}const Le=U.state.transmissionRenderTarget[j.id],Fe=j.viewport||xe;Le.setSize(Fe.z*W.transmissionResolutionScale,Fe.w*W.transmissionResolutionScale);const De=W.getRenderTarget(),Ge=W.getActiveCubeFace(),He=W.getActiveMipmapLevel();W.setRenderTarget(Le),W.getClearColor(tt),nt=W.getClearAlpha(),nt<1&&W.setClearColor(16777215,.5),W.clear(),qt&&it.render(ne);const Qe=W.toneMapping;W.toneMapping=Xi;const rt=j.viewport;if(j.viewport!==void 0&&(j.viewport=void 0),U.setupLightsView(j),St===!0&&Ye.setGlobalState(W.clippingPlanes,j),us(A,ne,j),le.updateMultisampleRenderTarget(Le),le.updateRenderTargetMipmap(Le),wt.has("WEBGL_multisampled_render_to_texture")===!1){let qe=!1;for(let Tt=0,Jt=k.length;Tt<Jt;Tt++){const Xt=k[Tt],{object:Ot,geometry:Pt,material:ze,group:Un}=Xt;if(ze.side===xa&&Ot.layers.test(j.layers)){const dt=ze.side;ze.side=Yn,ze.needsUpdate=!0,Aa(Ot,ne,j,Pt,ze,Un),ze.side=dt,ze.needsUpdate=!0,qe=!0}}qe===!0&&(le.updateMultisampleRenderTarget(Le),le.updateRenderTargetMipmap(Le))}W.setRenderTarget(De,Ge,He),W.setClearColor(tt,nt),rt!==void 0&&(j.viewport=rt),W.toneMapping=Qe}function us(A,k,ne){const j=k.isScene===!0?k.overrideMaterial:null;for(let ee=0,Le=A.length;ee<Le;ee++){const Fe=A[ee],{object:De,geometry:Ge,group:He}=Fe;let Qe=Fe.material;Qe.allowOverride===!0&&j!==null&&(Qe=j),De.layers.test(ne.layers)&&Aa(De,k,ne,Ge,Qe,He)}}function Aa(A,k,ne,j,ee,Le){A.onBeforeRender(W,k,ne,j,ee,Le),A.modelViewMatrix.multiplyMatrices(ne.matrixWorldInverse,A.matrixWorld),A.normalMatrix.getNormalMatrix(A.modelViewMatrix),ee.onBeforeRender(W,k,ne,j,A,Le),ee.transparent===!0&&ee.side===xa&&ee.forceSinglePass===!1?(ee.side=Yn,ee.needsUpdate=!0,W.renderBufferDirect(ne,k,j,ee,A,Le),ee.side=ls,ee.needsUpdate=!0,W.renderBufferDirect(ne,k,j,ee,A,Le),ee.side=xa):W.renderBufferDirect(ne,k,j,ee,A,Le),A.onAfterRender(W,k,ne,j,ee,Le)}function Ra(A,k,ne){k.isScene!==!0&&(k=ln);const j=te.get(A),ee=U.state.lights,Le=U.state.shadowsArray,Fe=ee.state.version,De=be.getParameters(A,ee.state,Le,k,ne,U.state.lightProbeGridArray),Ge=be.getProgramCacheKey(De);let He=j.programs;j.environment=A.isMeshStandardMaterial||A.isMeshLambertMaterial||A.isMeshPhongMaterial?k.environment:null,j.fog=k.fog;const Qe=A.isMeshStandardMaterial||A.isMeshLambertMaterial&&!A.envMap||A.isMeshPhongMaterial&&!A.envMap;j.envMap=ye.get(A.envMap||j.environment,Qe),j.envMapRotation=j.environment!==null&&A.envMap===null?k.environmentRotation:A.envMapRotation,He===void 0&&(A.addEventListener("dispose",Qn),He=new Map,j.programs=He);let rt=He.get(Ge);if(rt!==void 0){if(j.currentProgram===rt&&j.lightsStateVersion===Fe)return Qi(A,De),rt}else De.uniforms=be.getUniforms(A),Z!==null&&A.isNodeMaterial&&Z.build(A,ne,De),A.onBeforeCompile(De,W),rt=be.acquireProgram(De,Ge),He.set(Ge,rt),j.uniforms=De.uniforms;const qe=j.uniforms;return(!A.isShaderMaterial&&!A.isRawShaderMaterial||A.clipping===!0)&&(qe.clippingPlanes=Ye.uniform),Qi(A,De),j.needsLights=rl(A),j.lightsStateVersion=Fe,j.needsLights&&(qe.ambientLightColor.value=ee.state.ambient,qe.lightProbe.value=ee.state.probe,qe.directionalLights.value=ee.state.directional,qe.directionalLightShadows.value=ee.state.directionalShadow,qe.spotLights.value=ee.state.spot,qe.spotLightShadows.value=ee.state.spotShadow,qe.rectAreaLights.value=ee.state.rectArea,qe.ltc_1.value=ee.state.rectAreaLTC1,qe.ltc_2.value=ee.state.rectAreaLTC2,qe.pointLights.value=ee.state.point,qe.pointLightShadows.value=ee.state.pointShadow,qe.hemisphereLights.value=ee.state.hemi,qe.directionalShadowMatrix.value=ee.state.directionalShadowMatrix,qe.spotLightMatrix.value=ee.state.spotLightMatrix,qe.spotLightMap.value=ee.state.spotLightMap,qe.pointShadowMatrix.value=ee.state.pointShadowMatrix),j.lightProbeGrid=U.state.lightProbeGridArray.length>0,j.currentProgram=rt,j.uniformsList=null,rt}function Ki(A){if(A.uniformsList===null){const k=A.currentProgram.getUniforms();A.uniformsList=qc.seqWithValue(k.seq,A.uniforms)}return A.uniformsList}function Qi(A,k){const ne=te.get(A);ne.outputColorSpace=k.outputColorSpace,ne.batching=k.batching,ne.batchingColor=k.batchingColor,ne.instancing=k.instancing,ne.instancingColor=k.instancingColor,ne.instancingMorph=k.instancingMorph,ne.skinning=k.skinning,ne.morphTargets=k.morphTargets,ne.morphNormals=k.morphNormals,ne.morphColors=k.morphColors,ne.morphTargetsCount=k.morphTargetsCount,ne.numClippingPlanes=k.numClippingPlanes,ne.numIntersection=k.numClipIntersection,ne.vertexAlphas=k.vertexAlphas,ne.vertexTangents=k.vertexTangents,ne.toneMapping=k.toneMapping}function fs(A,k){if(A.length===0)return null;if(A.length===1)return A[0].texture!==null?A[0]:null;C.setFromMatrixPosition(k.matrixWorld);for(let ne=0,j=A.length;ne<j;ne++){const ee=A[ne];if(ee.texture!==null&&ee.boundingBox.containsPoint(C))return ee}return null}function Ca(A,k,ne,j,ee){k.isScene!==!0&&(k=ln),le.resetTextureUnits();const Le=k.fog,Fe=j.isMeshStandardMaterial||j.isMeshLambertMaterial||j.isMeshPhongMaterial?k.environment:null,De=$===null?W.outputColorSpace:$.isXRRenderTarget===!0?$.texture.colorSpace:yt.workingColorSpace,Ge=j.isMeshStandardMaterial||j.isMeshLambertMaterial&&!j.envMap||j.isMeshPhongMaterial&&!j.envMap,He=ye.get(j.envMap||Fe,Ge),Qe=j.vertexColors===!0&&!!ne.attributes.color&&ne.attributes.color.itemSize===4,rt=!!ne.attributes.tangent&&(!!j.normalMap||j.anisotropy>0),qe=!!ne.morphAttributes.position,Tt=!!ne.morphAttributes.normal,Jt=!!ne.morphAttributes.color;let Xt=Xi;j.toneMapped&&($===null||$.isXRRenderTarget===!0)&&(Xt=W.toneMapping);const Ot=ne.morphAttributes.position||ne.morphAttributes.normal||ne.morphAttributes.color,Pt=Ot!==void 0?Ot.length:0,ze=te.get(j),Un=U.state.lights;if(St===!0&&(ft===!0||A!==pe)){const Lt=A===pe&&j.id===se;Ye.setState(j,A,Lt)}let dt=!1;j.version===ze.__version?(ze.needsLights&&ze.lightsStateVersion!==Un.state.version||ze.outputColorSpace!==De||ee.isBatchedMesh&&ze.batching===!1||!ee.isBatchedMesh&&ze.batching===!0||ee.isBatchedMesh&&ze.batchingColor===!0&&ee.colorTexture===null||ee.isBatchedMesh&&ze.batchingColor===!1&&ee.colorTexture!==null||ee.isInstancedMesh&&ze.instancing===!1||!ee.isInstancedMesh&&ze.instancing===!0||ee.isSkinnedMesh&&ze.skinning===!1||!ee.isSkinnedMesh&&ze.skinning===!0||ee.isInstancedMesh&&ze.instancingColor===!0&&ee.instanceColor===null||ee.isInstancedMesh&&ze.instancingColor===!1&&ee.instanceColor!==null||ee.isInstancedMesh&&ze.instancingMorph===!0&&ee.morphTexture===null||ee.isInstancedMesh&&ze.instancingMorph===!1&&ee.morphTexture!==null||ze.envMap!==He||j.fog===!0&&ze.fog!==Le||ze.numClippingPlanes!==void 0&&(ze.numClippingPlanes!==Ye.numPlanes||ze.numIntersection!==Ye.numIntersection)||ze.vertexAlphas!==Qe||ze.vertexTangents!==rt||ze.morphTargets!==qe||ze.morphNormals!==Tt||ze.morphColors!==Jt||ze.toneMapping!==Xt||ze.morphTargetsCount!==Pt||!!ze.lightProbeGrid!=U.state.lightProbeGridArray.length>0)&&(dt=!0):(dt=!0,ze.__version=j.version);let xn=ze.currentProgram;dt===!0&&(xn=Ra(j,k,ee),Z&&j.isNodeMaterial&&Z.onUpdateProgram(j,xn,ze));let Jn=!1,Mi=!1,$n=!1;const zt=xn.getUniforms(),$t=ze.uniforms;if(M.useProgram(xn.program)&&(Jn=!0,Mi=!0,$n=!0),j.id!==se&&(se=j.id,Mi=!0),ze.needsLights){const Lt=fs(U.state.lightProbeGridArray,ee);ze.lightProbeGrid!==Lt&&(ze.lightProbeGrid=Lt,Mi=!0)}if(Jn||pe!==A){M.buffers.depth.getReversed()&&A.reversedDepth!==!0&&(A._reversedDepth=!0,A.updateProjectionMatrix()),zt.setValue(X,"projectionMatrix",A.projectionMatrix),zt.setValue(X,"viewMatrix",A.matrixWorldInverse);const Oi=zt.map.cameraPosition;Oi!==void 0&&Oi.setValue(X,tn.setFromMatrixPosition(A.matrixWorld)),w.logarithmicDepthBuffer&&zt.setValue(X,"logDepthBufFC",2/(Math.log(A.far+1)/Math.LN2)),(j.isMeshPhongMaterial||j.isMeshToonMaterial||j.isMeshLambertMaterial||j.isMeshBasicMaterial||j.isMeshStandardMaterial||j.isShaderMaterial)&&zt.setValue(X,"isOrthographic",A.isOrthographicCamera===!0),pe!==A&&(pe=A,Mi=!0,$n=!0)}if(ze.needsLights&&(Un.state.directionalShadowMap.length>0&&zt.setValue(X,"directionalShadowMap",Un.state.directionalShadowMap,le),Un.state.spotShadowMap.length>0&&zt.setValue(X,"spotShadowMap",Un.state.spotShadowMap,le),Un.state.pointShadowMap.length>0&&zt.setValue(X,"pointShadowMap",Un.state.pointShadowMap,le)),ee.isSkinnedMesh){zt.setOptional(X,ee,"bindMatrix"),zt.setOptional(X,ee,"bindMatrixInverse");const Lt=ee.skeleton;Lt&&(Lt.boneTexture===null&&Lt.computeBoneTexture(),zt.setValue(X,"boneTexture",Lt.boneTexture,le))}ee.isBatchedMesh&&(zt.setOptional(X,ee,"batchingTexture"),zt.setValue(X,"batchingTexture",ee._matricesTexture,le),zt.setOptional(X,ee,"batchingIdTexture"),zt.setValue(X,"batchingIdTexture",ee._indirectTexture,le),zt.setOptional(X,ee,"batchingColorTexture"),ee._colorsTexture!==null&&zt.setValue(X,"batchingColorTexture",ee._colorsTexture,le));const yi=ne.morphAttributes;if((yi.position!==void 0||yi.normal!==void 0||yi.color!==void 0)&&G.update(ee,ne,xn),(Mi||ze.receiveShadow!==ee.receiveShadow)&&(ze.receiveShadow=ee.receiveShadow,zt.setValue(X,"receiveShadow",ee.receiveShadow)),(j.isMeshStandardMaterial||j.isMeshLambertMaterial||j.isMeshPhongMaterial)&&j.envMap===null&&k.environment!==null&&($t.envMapIntensity.value=k.environmentIntensity),$t.dfgLUT!==void 0&&($t.dfgLUT.value=TR()),Mi){if(zt.setValue(X,"toneMappingExposure",W.toneMappingExposure),ze.needsLights&&hn($t,$n),Le&&j.fog===!0&&Pe.refreshFogUniforms($t,Le),Pe.refreshMaterialUniforms($t,j,Me,Ae,U.state.transmissionRenderTarget[A.id]),ze.needsLights&&ze.lightProbeGrid){const Lt=ze.lightProbeGrid;$t.probesSH.value=Lt.texture,$t.probesMin.value.copy(Lt.boundingBox.min),$t.probesMax.value.copy(Lt.boundingBox.max),$t.probesResolution.value.copy(Lt.resolution)}qc.upload(X,Ki(ze),$t,le)}if(j.isShaderMaterial&&j.uniformsNeedUpdate===!0&&(qc.upload(X,Ki(ze),$t,le),j.uniformsNeedUpdate=!1),j.isSpriteMaterial&&zt.setValue(X,"center",ee.center),zt.setValue(X,"modelViewMatrix",ee.modelViewMatrix),zt.setValue(X,"normalMatrix",ee.normalMatrix),zt.setValue(X,"modelMatrix",ee.matrixWorld),j.uniformsGroups!==void 0){const Lt=j.uniformsGroups;for(let Oi=0,wa=Lt.length;Oi<wa;Oi++){const ds=Lt[Oi];ve.update(ds,xn),ve.bind(ds,xn)}}return xn}function hn(A,k){A.ambientLightColor.needsUpdate=k,A.lightProbe.needsUpdate=k,A.directionalLights.needsUpdate=k,A.directionalLightShadows.needsUpdate=k,A.pointLights.needsUpdate=k,A.pointLightShadows.needsUpdate=k,A.spotLights.needsUpdate=k,A.spotLightShadows.needsUpdate=k,A.rectAreaLights.needsUpdate=k,A.hemisphereLights.needsUpdate=k}function rl(A){return A.isMeshLambertMaterial||A.isMeshToonMaterial||A.isMeshPhongMaterial||A.isMeshStandardMaterial||A.isShadowMaterial||A.isShaderMaterial&&A.lights===!0}this.getActiveCubeFace=function(){return N},this.getActiveMipmapLevel=function(){return I},this.getRenderTarget=function(){return $},this.setRenderTargetTextures=function(A,k,ne){const j=te.get(A);j.__autoAllocateDepthBuffer=A.resolveDepthBuffer===!1,j.__autoAllocateDepthBuffer===!1&&(j.__useRenderToTexture=!1),te.get(A.texture).__webglTexture=k,te.get(A.depthTexture).__webglTexture=j.__autoAllocateDepthBuffer?void 0:ne,j.__hasExternalTextures=!0},this.setRenderTargetFramebuffer=function(A,k){const ne=te.get(A);ne.__webglFramebuffer=k,ne.__useDefaultFramebuffer=k===void 0},this.setRenderTarget=function(A,k=0,ne=0){$=A,N=k,I=ne;let j=null,ee=!1,Le=!1;if(A){const De=te.get(A);if(De.__useDefaultFramebuffer!==void 0){M.bindFramebuffer(X.FRAMEBUFFER,De.__webglFramebuffer),xe.copy(A.viewport),Se.copy(A.scissor),Te=A.scissorTest,M.viewport(xe),M.scissor(Se),M.setScissorTest(Te),se=-1;return}else if(De.__webglFramebuffer===void 0)le.setupRenderTarget(A);else if(De.__hasExternalTextures)le.rebindTextures(A,te.get(A.texture).__webglTexture,te.get(A.depthTexture).__webglTexture);else if(A.depthBuffer){const Qe=A.depthTexture;if(De.__boundDepthTexture!==Qe){if(Qe!==null&&te.has(Qe)&&(A.width!==Qe.image.width||A.height!==Qe.image.height))throw new Error("THREE.WebGLRenderer: Attached DepthTexture is initialized to the incorrect size.");le.setupDepthRenderbuffer(A)}}const Ge=A.texture;(Ge.isData3DTexture||Ge.isDataArrayTexture||Ge.isCompressedArrayTexture)&&(Le=!0);const He=te.get(A).__webglFramebuffer;A.isWebGLCubeRenderTarget?(Array.isArray(He[k])?j=He[k][ne]:j=He[k],ee=!0):A.samples>0&&le.useMultisampledRTT(A)===!1?j=te.get(A).__webglMultisampledFramebuffer:Array.isArray(He)?j=He[ne]:j=He,xe.copy(A.viewport),Se.copy(A.scissor),Te=A.scissorTest}else xe.copy(Ze).multiplyScalar(Me).floor(),Se.copy(Yt).multiplyScalar(Me).floor(),Te=ut;if(ne!==0&&(j=de),M.bindFramebuffer(X.FRAMEBUFFER,j)&&M.drawBuffers(A,j),M.viewport(xe),M.scissor(Se),M.setScissorTest(Te),ee){const De=te.get(A.texture);X.framebufferTexture2D(X.FRAMEBUFFER,X.COLOR_ATTACHMENT0,X.TEXTURE_CUBE_MAP_POSITIVE_X+k,De.__webglTexture,ne)}else if(Le){const De=k;for(let Ge=0;Ge<A.textures.length;Ge++){const He=te.get(A.textures[Ge]);X.framebufferTextureLayer(X.FRAMEBUFFER,X.COLOR_ATTACHMENT0+Ge,He.__webglTexture,ne,De)}}else if(A!==null&&ne!==0){const De=te.get(A.texture);X.framebufferTexture2D(X.FRAMEBUFFER,X.COLOR_ATTACHMENT0,X.TEXTURE_2D,De.__webglTexture,ne)}se=-1},this.readRenderTargetPixels=function(A,k,ne,j,ee,Le,Fe,De=0){if(!(A&&A.isWebGLRenderTarget)){bt("WebGLRenderer.readRenderTargetPixels: renderTarget is not THREE.WebGLRenderTarget.");return}let Ge=te.get(A).__webglFramebuffer;if(A.isWebGLCubeRenderTarget&&Fe!==void 0&&(Ge=Ge[Fe]),Ge){M.bindFramebuffer(X.FRAMEBUFFER,Ge);try{const He=A.textures[De],Qe=He.format,rt=He.type;if(A.textures.length>1&&X.readBuffer(X.COLOR_ATTACHMENT0+De),!w.textureFormatReadable(Qe)){bt("WebGLRenderer.readRenderTargetPixels: renderTarget is not in RGBA or implementation defined format.");return}if(!w.textureTypeReadable(rt)){bt("WebGLRenderer.readRenderTargetPixels: renderTarget is not in UnsignedByteType or implementation defined type.");return}k>=0&&k<=A.width-j&&ne>=0&&ne<=A.height-ee&&X.readPixels(k,ne,j,ee,Re.convert(Qe),Re.convert(rt),Le)}finally{const He=$!==null?te.get($).__webglFramebuffer:null;M.bindFramebuffer(X.FRAMEBUFFER,He)}}},this.readRenderTargetPixelsAsync=async function(A,k,ne,j,ee,Le,Fe,De=0){if(!(A&&A.isWebGLRenderTarget))throw new Error("THREE.WebGLRenderer.readRenderTargetPixels: renderTarget is not THREE.WebGLRenderTarget.");let Ge=te.get(A).__webglFramebuffer;if(A.isWebGLCubeRenderTarget&&Fe!==void 0&&(Ge=Ge[Fe]),Ge)if(k>=0&&k<=A.width-j&&ne>=0&&ne<=A.height-ee){M.bindFramebuffer(X.FRAMEBUFFER,Ge);const He=A.textures[De],Qe=He.format,rt=He.type;if(A.textures.length>1&&X.readBuffer(X.COLOR_ATTACHMENT0+De),!w.textureFormatReadable(Qe))throw new Error("THREE.WebGLRenderer.readRenderTargetPixelsAsync: renderTarget is not in RGBA or implementation defined format.");if(!w.textureTypeReadable(rt))throw new Error("THREE.WebGLRenderer.readRenderTargetPixelsAsync: renderTarget is not in UnsignedByteType or implementation defined type.");const qe=X.createBuffer();X.bindBuffer(X.PIXEL_PACK_BUFFER,qe),X.bufferData(X.PIXEL_PACK_BUFFER,Le.byteLength,X.STREAM_READ),X.readPixels(k,ne,j,ee,Re.convert(Qe),Re.convert(rt),0);const Tt=$!==null?te.get($).__webglFramebuffer:null;M.bindFramebuffer(X.FRAMEBUFFER,Tt);const Jt=X.fenceSync(X.SYNC_GPU_COMMANDS_COMPLETE,0);return X.flush(),await Ly(X,Jt,4),X.bindBuffer(X.PIXEL_PACK_BUFFER,qe),X.getBufferSubData(X.PIXEL_PACK_BUFFER,0,Le),X.deleteBuffer(qe),X.deleteSync(Jt),Le}else throw new Error("THREE.WebGLRenderer.readRenderTargetPixelsAsync: requested read bounds are out of range.")},this.copyFramebufferToTexture=function(A,k=null,ne=0){const j=Math.pow(2,-ne),ee=Math.floor(A.image.width*j),Le=Math.floor(A.image.height*j),Fe=k!==null?k.x:0,De=k!==null?k.y:0;le.setTexture2D(A,0),X.copyTexSubImage2D(X.TEXTURE_2D,ne,0,0,Fe,De,ee,Le),M.unbindTexture()},this.copyTextureToTexture=function(A,k,ne=null,j=null,ee=0,Le=0){let Fe,De,Ge,He,Qe,rt,qe,Tt,Jt;const Xt=A.isCompressedTexture?A.mipmaps[Le]:A.image;if(ne!==null)Fe=ne.max.x-ne.min.x,De=ne.max.y-ne.min.y,Ge=ne.isBox3?ne.max.z-ne.min.z:1,He=ne.min.x,Qe=ne.min.y,rt=ne.isBox3?ne.min.z:0;else{const $t=Math.pow(2,-ee);Fe=Math.floor(Xt.width*$t),De=Math.floor(Xt.height*$t),A.isDataArrayTexture?Ge=Xt.depth:A.isData3DTexture?Ge=Math.floor(Xt.depth*$t):Ge=1,He=0,Qe=0,rt=0}j!==null?(qe=j.x,Tt=j.y,Jt=j.z):(qe=0,Tt=0,Jt=0);const Ot=Re.convert(k.format),Pt=Re.convert(k.type);let ze;k.isData3DTexture?(le.setTexture3D(k,0),ze=X.TEXTURE_3D):k.isDataArrayTexture||k.isCompressedArrayTexture?(le.setTexture2DArray(k,0),ze=X.TEXTURE_2D_ARRAY):(le.setTexture2D(k,0),ze=X.TEXTURE_2D),M.activeTexture(X.TEXTURE0),M.pixelStorei(X.UNPACK_FLIP_Y_WEBGL,k.flipY),M.pixelStorei(X.UNPACK_PREMULTIPLY_ALPHA_WEBGL,k.premultiplyAlpha),M.pixelStorei(X.UNPACK_ALIGNMENT,k.unpackAlignment);const Un=M.getParameter(X.UNPACK_ROW_LENGTH),dt=M.getParameter(X.UNPACK_IMAGE_HEIGHT),xn=M.getParameter(X.UNPACK_SKIP_PIXELS),Jn=M.getParameter(X.UNPACK_SKIP_ROWS),Mi=M.getParameter(X.UNPACK_SKIP_IMAGES);M.pixelStorei(X.UNPACK_ROW_LENGTH,Xt.width),M.pixelStorei(X.UNPACK_IMAGE_HEIGHT,Xt.height),M.pixelStorei(X.UNPACK_SKIP_PIXELS,He),M.pixelStorei(X.UNPACK_SKIP_ROWS,Qe),M.pixelStorei(X.UNPACK_SKIP_IMAGES,rt);const $n=A.isDataArrayTexture||A.isData3DTexture,zt=k.isDataArrayTexture||k.isData3DTexture;if(A.isDepthTexture){const $t=te.get(A),yi=te.get(k),Lt=te.get($t.__renderTarget),Oi=te.get(yi.__renderTarget);M.bindFramebuffer(X.READ_FRAMEBUFFER,Lt.__webglFramebuffer),M.bindFramebuffer(X.DRAW_FRAMEBUFFER,Oi.__webglFramebuffer);for(let wa=0;wa<Ge;wa++)$n&&(X.framebufferTextureLayer(X.READ_FRAMEBUFFER,X.COLOR_ATTACHMENT0,te.get(A).__webglTexture,ee,rt+wa),X.framebufferTextureLayer(X.DRAW_FRAMEBUFFER,X.COLOR_ATTACHMENT0,te.get(k).__webglTexture,Le,Jt+wa)),X.blitFramebuffer(He,Qe,Fe,De,qe,Tt,Fe,De,X.DEPTH_BUFFER_BIT,X.NEAREST);M.bindFramebuffer(X.READ_FRAMEBUFFER,null),M.bindFramebuffer(X.DRAW_FRAMEBUFFER,null)}else if(ee!==0||A.isRenderTargetTexture||te.has(A)){const $t=te.get(A),yi=te.get(k);M.bindFramebuffer(X.READ_FRAMEBUFFER,he),M.bindFramebuffer(X.DRAW_FRAMEBUFFER,K);for(let Lt=0;Lt<Ge;Lt++)$n?X.framebufferTextureLayer(X.READ_FRAMEBUFFER,X.COLOR_ATTACHMENT0,$t.__webglTexture,ee,rt+Lt):X.framebufferTexture2D(X.READ_FRAMEBUFFER,X.COLOR_ATTACHMENT0,X.TEXTURE_2D,$t.__webglTexture,ee),zt?X.framebufferTextureLayer(X.DRAW_FRAMEBUFFER,X.COLOR_ATTACHMENT0,yi.__webglTexture,Le,Jt+Lt):X.framebufferTexture2D(X.DRAW_FRAMEBUFFER,X.COLOR_ATTACHMENT0,X.TEXTURE_2D,yi.__webglTexture,Le),ee!==0?X.blitFramebuffer(He,Qe,Fe,De,qe,Tt,Fe,De,X.COLOR_BUFFER_BIT,X.NEAREST):zt?X.copyTexSubImage3D(ze,Le,qe,Tt,Jt+Lt,He,Qe,Fe,De):X.copyTexSubImage2D(ze,Le,qe,Tt,He,Qe,Fe,De);M.bindFramebuffer(X.READ_FRAMEBUFFER,null),M.bindFramebuffer(X.DRAW_FRAMEBUFFER,null)}else zt?A.isDataTexture||A.isData3DTexture?X.texSubImage3D(ze,Le,qe,Tt,Jt,Fe,De,Ge,Ot,Pt,Xt.data):k.isCompressedArrayTexture?X.compressedTexSubImage3D(ze,Le,qe,Tt,Jt,Fe,De,Ge,Ot,Xt.data):X.texSubImage3D(ze,Le,qe,Tt,Jt,Fe,De,Ge,Ot,Pt,Xt):A.isDataTexture?X.texSubImage2D(X.TEXTURE_2D,Le,qe,Tt,Fe,De,Ot,Pt,Xt.data):A.isCompressedTexture?X.compressedTexSubImage2D(X.TEXTURE_2D,Le,qe,Tt,Xt.width,Xt.height,Ot,Xt.data):X.texSubImage2D(X.TEXTURE_2D,Le,qe,Tt,Fe,De,Ot,Pt,Xt);M.pixelStorei(X.UNPACK_ROW_LENGTH,Un),M.pixelStorei(X.UNPACK_IMAGE_HEIGHT,dt),M.pixelStorei(X.UNPACK_SKIP_PIXELS,xn),M.pixelStorei(X.UNPACK_SKIP_ROWS,Jn),M.pixelStorei(X.UNPACK_SKIP_IMAGES,Mi),Le===0&&k.generateMipmaps&&X.generateMipmap(ze),M.unbindTexture()},this.initRenderTarget=function(A){te.get(A).__webglFramebuffer===void 0&&le.setupRenderTarget(A)},this.initTexture=function(A){A.isCubeTexture?le.setTextureCube(A,0):A.isData3DTexture?le.setTexture3D(A,0):A.isDataArrayTexture||A.isCompressedArrayTexture?le.setTexture2DArray(A,0):le.setTexture2D(A,0),M.unbindTexture()},this.resetState=function(){N=0,I=0,$=null,M.reset(),Oe.reset()},typeof __THREE_DEVTOOLS__<"u"&&__THREE_DEVTOOLS__.dispatchEvent(new CustomEvent("observe",{detail:this}))}get coordinateSystem(){return ki}get outputColorSpace(){return this._outputColorSpace}set outputColorSpace(e){this._outputColorSpace=e;const i=this.getContext();i.drawingBufferColorSpace=yt._getDrawingBufferColorSpace(e),i.unpackColorSpace=yt._getUnpackColorSpace()}}const Y_={light:{fogColor:15659775,fogDensity:.015,meshColor:6514417,meshOpacity:.12,particleOpacity:.45,colorStart:6514417,colorEnd:440020},dark:{fogColor:592139,fogDensity:.028,meshColor:1357990,meshOpacity:.22,particleOpacity:.65,colorStart:1357990,colorEnd:3900150}};function RR({theme:s}){const e=mt.useRef(null),i=mt.useRef(null),r=mt.useRef(s);return r.current=s,mt.useEffect(()=>{const l=e.current;if(!l)return;const u=l,d=window.matchMedia("(prefers-reduced-motion: reduce)").matches,h=l.clientWidth||window.innerWidth,m=l.clientHeight||window.innerHeight,p=Y_[r.current];let v;try{v=new AR({alpha:!0,antialias:!0})}catch{return}const x=new cE;x.fog=new Kh(p.fogColor,p.fogDensity);const g=new vi(75,h/m,.1,1e3);g.position.y=5,g.position.z=20,g.lookAt(0,0,0),v.setSize(h,m),v.setPixelRatio(Math.min(window.devicePixelRatio,2)),v.setClearColor(0,0),l.appendChild(v.domElement);const E=new il(200,200,48,48),T=E.attributes.position,D=T.count,y=new Float32Array(D*2);for(let se=0;se<D;se++)y[se*2]=T.getX(se),y[se*2+1]=T.getY(se);const S=new Qh({color:p.meshColor,wireframe:!0,transparent:!0,opacity:p.meshOpacity}),z=new Ui(E,S);z.rotation.x=-Math.PI/2,z.position.y=-2,x.add(z);const F=new Ui(E,S);F.rotation.x=-Math.PI/2,F.position.y=-2,F.position.z=-25,x.add(F);const C=500,O=new Li,U=new Float32Array(C*3),B=new Float32Array(C*3),b=new Float32Array(C*3),L=new Et(p.colorStart),W=new Et(p.colorEnd);for(let se=0;se<C;se++){const pe=se*3;U[pe]=(Math.random()-.5)*90,U[pe+1]=Math.random()*22,U[pe+2]=(Math.random()-.5)*90,B[pe]=(Math.random()-.5)*.04,B[pe+1]=.06+Math.random()*.12,B[pe+2]=(Math.random()-.5)*.04;const xe=L.clone().lerp(W,Math.random());b[pe]=xe.r,b[pe+1]=xe.g,b[pe+2]=xe.b}O.setAttribute("position",new Si(U,3)),O.setAttribute("color",new Si(b,3));const V=new Av({size:.18,vertexColors:!0,transparent:!0,opacity:p.particleOpacity,blending:Yd,sizeAttenuation:!0}),Z=new xE(O,V);x.add(Z);const de=new DE;let he=0;function K(){const se=de.getElapsedTime();for(let Se=0;Se<D;Se++){const Te=y[Se*2],tt=y[Se*2+1],nt=Math.sin(Te*.12+se*1.4)*Math.cos(tt*.12+se*1.1)*2.2+Math.sin(Te*.25+tt*.2+se*2)*.9;T.setZ(Se,nt)}T.needsUpdate=!0;const pe=se*9;z.position.z=n_.euclideanModulo(pe,25),F.position.z=n_.euclideanModulo(pe,25)-25;const xe=O.attributes.position.array;for(let Se=0;Se<C;Se++){const Te=Se*3;xe[Te]+=B[Te],xe[Te+1]+=B[Te+1],xe[Te+2]+=B[Te+2],xe[Te+1]>24&&(xe[Te+1]=-2,xe[Te]=(Math.random()-.5)*90,xe[Te+2]=(Math.random()-.5)*90),Math.abs(xe[Te])>48&&(xe[Te]*=-.95),Math.abs(xe[Te+2])>48&&(xe[Te+2]*=-.95)}O.attributes.position.needsUpdate=!0,Z.rotation.y=se*.08,Z.rotation.z=Math.sin(se*.25)*.06,g.position.x=Math.sin(se*.35)*2.8,g.position.y=5+Math.sin(se*.5)*.35,g.lookAt(0,-.4+Math.sin(se*.2)*.15,Math.sin(se*.15)*1.5),v.render(x,g)}function N(){K(),he=requestAnimationFrame(N)}d?K():N();function I(){const se=u.clientWidth||window.innerWidth,pe=u.clientHeight||window.innerHeight;g.aspect=se/pe,g.updateProjectionMatrix(),v.setSize(se,pe),d&&K()}window.addEventListener("resize",I);function $(se){const pe=Y_[se],xe=x.fog;xe.color.setHex(pe.fogColor),xe.density=pe.fogDensity,S.color.setHex(pe.meshColor),S.opacity=pe.meshOpacity,V.opacity=pe.particleOpacity,L.setHex(pe.colorStart),W.setHex(pe.colorEnd);const Se=O.attributes.color,Te=Se.array;for(let tt=0;tt<C;tt++){const nt=tt*3,ue=L.clone().lerp(W,Math.random());Te[nt]=ue.r,Te[nt+1]=ue.g,Te[nt+2]=ue.b}Se.needsUpdate=!0,d&&K()}return i.current={applyTheme:$},()=>{window.removeEventListener("resize",I),cancelAnimationFrame(he),v.domElement.parentNode===u&&u.removeChild(v.domElement),E.dispose(),S.dispose(),O.dispose(),V.dispose(),v.dispose(),i.current=null}},[]),mt.useEffect(()=>{var l;(l=i.current)==null||l.applyTheme(s)},[s]),We.jsx("div",{ref:e,className:"wave-bg","data-theme":s,"aria-hidden":"true"})}const CR=mt.lazy(()=>Zi(()=>import("./HomeScreenPage-N-yTQtRP.js"),__vite__mapDeps([0,1])).then(s=>({default:s.HomePage}))),wR=mt.lazy(()=>Zi(()=>import("./HomePage-u1uAmGcx.js"),__vite__mapDeps([2,3,4,5,6,7,1])).then(s=>({default:s.HomePage}))),DR=mt.lazy(()=>Zi(()=>import("./PoolPage-Cb2ZXxxS.js"),__vite__mapDeps([8,3,9,10,11,12,7,1])).then(s=>({default:s.PoolPage}))),UR=mt.lazy(()=>Zi(()=>import("./HistoryPage-GU4kEM3p.js"),__vite__mapDeps([13,3,10,9,11,12,7,1])).then(s=>({default:s.HistoryPage}))),LR=mt.lazy(()=>Zi(()=>import("./RecordsPage-CSHEUEjL.js"),__vite__mapDeps([14,3,9,10,15,5,7,1])).then(s=>({default:s.RecordsPage}))),NR=mt.lazy(()=>Zi(()=>import("./MatrixPage-imJEylDp.js"),__vite__mapDeps([16,3,5,4,1])).then(s=>({default:s.MatrixPage}))),OR=mt.lazy(()=>Zi(()=>import("./MetricsPage-BCKAQA53.js"),__vite__mapDeps([17,10,3,1])).then(s=>({default:s.MetricsPage}))),PR=mt.lazy(()=>Zi(()=>import("./StrategiesPage-Dvs6zrRD.js"),__vite__mapDeps([18,3,10,12,7,1])).then(s=>({default:s.StrategiesPage}))),zR=mt.lazy(()=>Zi(()=>import("./ConfigPage-jxXaLMR5.js"),__vite__mapDeps([19,3,10,6,1])).then(s=>({default:s.ConfigPage}))),FR=mt.lazy(()=>Zi(()=>import("./RunDetailPage-BfqdUr8c.js"),__vite__mapDeps([20,3,15,5,1])).then(s=>({default:s.RunDetailPage})));function BR({section:s}){switch(s){case"home":return We.jsx(CR,{});case"dashboard":return We.jsx(wR,{});case"pool":return We.jsx(DR,{});case"history":return We.jsx(UR,{});case"records":return We.jsx(LR,{});case"matrix":return We.jsx(NR,{});case"metrics":return We.jsx(OR,{});case"strategies":return We.jsx(PR,{});case"config":return We.jsx(zR,{})}}function Z_(){return We.jsxs("div",{className:"panel",children:[We.jsx("div",{className:"skeleton",style:{height:28,width:180,marginBottom:"var(--space-4)"}}),We.jsx("div",{className:"skeleton",style:{height:16,width:"100%",marginBottom:"var(--space-2)"}}),We.jsx("div",{className:"skeleton",style:{height:16,width:"92%"}})]})}function IR(){const{pathname:s,section:e,navigate:i,navigateSection:r}=QM(),{theme:l,toggleTheme:u}=JM(),[d,h]=mt.useState(0),m=s==="/run",p=()=>h(v=>v+1);return We.jsxs(WM,{children:[We.jsx(RR,{theme:l}),We.jsxs("div",{className:"app-shell",children:[We.jsxs("header",{className:"topbar",children:[We.jsxs("div",{className:"topbar__brand",onClick:()=>i("/"),role:"button",tabIndex:0,children:[We.jsx("span",{className:"topbar__brand-badge",children:"A"}),We.jsx("span",{children:"短线驾驶舱"})]}),We.jsx("nav",{className:"topbar__nav",children:qM.map(v=>{const x=v.icon,g=ZM(v.section,s);return We.jsxs("button",{className:`topbar__nav-item${g?" topbar__nav-item--active":""}`,onClick:()=>r(v.section),children:[We.jsx(x,{size:16}),v.label]},v.section)})}),We.jsxs("div",{className:"topbar__actions",children:[We.jsx(HR,{onClick:()=>i("/run")}),We.jsx("button",{className:"icon-button",title:"通知","aria-label":"通知",children:We.jsx(lM,{size:16})}),We.jsx("button",{className:"icon-button",onClick:p,title:"刷新当前页","aria-label":"刷新",children:We.jsx(wM,{size:16})}),We.jsx("button",{className:"icon-button",onClick:u,title:l==="dark"?"切换到浅色":"切换到深色","aria-label":"切换主题",children:l==="dark"?We.jsx(NM,{size:16}):We.jsx(RM,{size:16})})]})]}),We.jsx("main",{className:"app-main",children:m?We.jsx(mt.Suspense,{fallback:We.jsx(Z_,{}),children:We.jsx(FR,{refreshSignal:d})}):We.jsx(mt.Suspense,{fallback:We.jsx(Z_,{}),children:We.jsx(BR,{section:e})})})]})]})}function HR({onClick:s}){return We.jsxs("button",{className:"run-cta",onClick:s,children:[We.jsx(rM,{size:15}),"运行推荐"]})}function GR(){return We.jsx(IR,{})}J_("dark");const Hv=document.getElementById("root");if(!Hv)throw new Error("Missing #root element");QS.createRoot(Hv).render(We.jsx(mt.StrictMode,{children:We.jsx(GR,{})}));export{rM as A,dM as C,gM as H,V0 as L,wM as R,UM as S,W0 as a,yM as b,In as c,SM as d,vM as e,We as j,kR as q,mt as r,QM as u};
