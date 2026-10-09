class PcmDownsampler extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.carry = 0;
    this.samples = [];
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    for (let i = 0; i < channel.length; i += 1) {
      this.carry += 1;
      if (this.carry < this.ratio) continue;
      this.carry -= this.ratio;
      const sample = Math.max(-1, Math.min(1, channel[i]));
      this.samples.push(sample < 0 ? sample * 0x8000 : sample * 0x7fff);
    }
    if (this.samples.length >= 1600) {
      const pcm = new Int16Array(this.samples);
      this.samples = [];
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }
    return true;
  }
}

registerProcessor("pcm-downsampler", PcmDownsampler);
