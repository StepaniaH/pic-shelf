// One interval after a successful display; hidden, paused and pending work stop it.
export class PlaybackClock {
  constructor(seconds,onTick,{setTimer=(fn,ms)=>setTimeout(fn,ms),clearTimer=id=>clearTimeout(id)}={}) {
    this.delay=seconds*1000;this.onTick=onTick;this.setTimer=setTimer;this.clearTimer=clearTimer;
    this.flags={paused:false,hidden:false,busy:false,ready:false};this.timer=null;this.closed=false;
  }
  update(changes) {
    Object.assign(this.flags,changes);this.clearTimer(this.timer);this.timer=null;
    if(this.closed || this.delay<=0 || this.flags.paused || this.flags.hidden || this.flags.busy || !this.flags.ready)return;
    this.timer=this.setTimer(()=>{this.timer=null;this.flags.ready=false;this.onTick();},this.delay);
  }
  close(){this.closed=true;this.clearTimer(this.timer);this.timer=null;}
}
