from posixpath import split
from attrdict import AttrDict
from losses.loss_util import get_lossfns
from utils import AverageMeter
# from models.HY_IITP_ESNet_framebyframe import HY_IITP_ESNet1
from models.HY_IITP_ESNet_framebyframe import HY_IITP_ESNet2_framebyframe
from models.HY_IITP_ESNet_framebyframe import HY_IITP_ESNet3_framebyframe
import argparse, json, numpy as np, os, time, torch, glob, natsort
import soundfile as sf
import scipy.signal as ss
import random
import glob
import librosa
import re
import csv
import pypesq
from tqdm import tqdm

random.seed(0)
np.random.seed(0)
torch.manual_seed(0)
torch.cuda.manual_seed(0)

mode1 = 'test'
min_len = 128000

def ERLE(y, e, vad_src_idx, vad_dst_idx):
    
    y_erle = torch.cat((y[:vad_src_idx], y[vad_dst_idx:]), 0)
    e_erle = torch.cat((e[:vad_src_idx], e[vad_dst_idx:]), 0)
    
    erle = 10 * torch.log10( torch.mean(y_erle ** 2) /  torch.mean(e_erle ** 2) )
    return erle

def vad(buf):
    src_idx = 0
    dst_idx = buf.shape[0]
    for i in range(buf.shape[0]):
        if abs(buf[i]) > 1e-8:
            src_idx = i
            break

    for i in range(buf.shape[0]-1, -1, -1):
        if abs(buf[i]) > 1e-8:
            dst_idx = i
            break
    
    return src_idx, dst_idx

def power_amplifier_clipping(buf, max_val):
    # clipping
    buf = torch.clamp(buf, min=-max_val, max=max_val)
    return buf

def mic_clipping(buf, max_val):
    # clipping
    buf = torch.clamp(buf, min=-max_val, max=max_val)
    return buf

def loudspeaker_asymmetric_nonlinear(buf):
    # b(n)
    buf_b = 1.5 * buf - 0.3 * (buf**2)
    buf_nl = torch.zeros_like(buf)
    palpha = torch.where(buf_b > 0, 4.0, 0.5)
    buf_nl = 4 * (2 / (1 + torch.exp( -palpha * buf_b )) -1 )
    return buf_nl

def get_free_gpu():
    os.system('nvidia-smi -q -d Memory |grep -A4 GPU|grep Free >tmp')
    memory_available = [int(x.split()[2]) for x in open('tmp', 'r').readlines()]
    return np.argmax(memory_available)

class tester:
    def __init__(self, args, loss_type):
        self.model_name = args.model_name
        self.loss_name = args.loss_option
        self.dataset = args.dataset
        self.loss_type = loss_type
        self.hop_size = args.model_options.win_inc
        self.win_size = self.hop_size * 2
        self.max_val = 0.8
        self.chan = str(args.model_options.chan)
        self.arr  = args.feature_options.arr
        self.output_path = args.output_path +'/%s_%s_%s_%s_%s_%s'%(self.model_name, self.dataset, self.loss_name, str(self.loss_type), str(args.feature_options.chan), args.feature_options.arr)
        self.out_path_whole = self.output_path + '/' + self.model_name + '_output/'
        self.out_path_e = self.output_path + '/' + self.model_name + '_pesq_e/'
        self.out_path_s = self.output_path + '/' + self.model_name + '_pesq_s/'
        if args.cuda_option == "True":
            print("GPU mode on...")
            self.device = torch.device('cuda:0')
        else:
            self.device = torch.device('cpu')
        print("self.device:", self.device)

        # build model
        self.near_model = self.init_model(args, args.model_name, args.model_options)
        self.far_model = self.init_model(args, args.model_name, args.model_options)
        print("Loaded the model...")
        self.feature_options = args.feature_options
        self.sampling_rate = self.feature_options.sampling_rate
        self.data_path = self.feature_options.data_path
        
        load_path_near_src = "./DB/"+mode1+"/near_src/*_"+self.chan+"ch_"+self.arr+"/*.wav"
        self.load_path_near_src_nm_list = glob.glob(load_path_near_src)
        self.load_path_near_src_nm_list.sort()
        
        # RIRs shape: [4, 512]
        rir_409_LS2_mic = np.loadtxt("./rirs/"+self.chan+"ch/RIR_"+self.chan+"ch_409_LS2mic.txt", delimiter=",")
        rir_409_speaker2_mic = np.loadtxt("./rirs/"+self.chan+"ch/RIR_"+self.chan+"ch_409_speaker2mic.txt", delimiter=",")
        rir_819_LS2_mic = np.loadtxt("./rirs/"+self.chan+"ch/RIR_"+self.chan+"ch_819_LS2mic.txt", delimiter=",")
        rir_819_speaker2_mic = np.loadtxt("./rirs/"+self.chan+"ch/RIR_"+self.chan+"ch_819_speaker2mic.txt", delimiter=",")
        rir_cafe_LS2_mic = np.loadtxt("./rirs/"+self.chan+"ch/RIR_"+self.chan+"ch_cafe_LS2mic.txt", delimiter=",")
        rir_cafe_speaker2_mic = np.loadtxt("./rirs/"+self.chan+"ch/RIR_"+self.chan+"ch_cafe_speaker2mic.txt", delimiter=",")
        rir_car_LS2_mic = np.loadtxt("./rirs/"+self.chan+"ch/RIR_"+self.chan+"ch_car_LS2mic.txt", delimiter=",")
        rir_car_speaker2_mic = np.loadtxt("./rirs/"+self.chan+"ch/RIR_"+self.chan+"ch_car_speaker2mic.txt", delimiter=",")
        rir_home_LS2_mic = np.loadtxt("./rirs/"+self.chan+"ch/RIR_"+self.chan+"ch_home_LS2mic.txt", delimiter=",")
        rir_home_speaker2_mic = np.loadtxt("./rirs/"+self.chan+"ch/RIR_"+self.chan+"ch_home_speaker2mic.txt", delimiter=",")
        rir_hospital_LS2_mic = np.loadtxt("./rirs/"+self.chan+"ch/RIR_"+self.chan+"ch_hospital_LS2mic.txt", delimiter=",")
        rir_hospital_speaker2_mic = np.loadtxt("./rirs/"+self.chan+"ch/RIR_"+self.chan+"ch_hospital_speaker2mic.txt", delimiter=",")
        rir_meeting_LS2_mic = np.loadtxt("./rirs/"+self.chan+"ch/RIR_"+self.chan+"ch_meeting_LS2mic.txt", delimiter=",")
        rir_meeting_speaker2_mic = np.loadtxt("./rirs/"+self.chan+"ch/RIR_"+self.chan+"ch_meeting_speaker2mic.txt", delimiter=",")
        rir_seminar_LS2_mic = np.loadtxt("./rirs/"+self.chan+"ch/RIR_"+self.chan+"ch_seminar_LS2mic.txt", delimiter=",")
        rir_seminar_speaker2_mic = np.loadtxt("./rirs/"+self.chan+"ch/RIR_"+self.chan+"ch_seminar_speaker2mic.txt", delimiter=",")
        self.rir_LS2mic_dict = {"409":rir_409_LS2_mic, "819":rir_819_LS2_mic, "cafe":rir_cafe_LS2_mic,
                                "car":rir_car_LS2_mic, "home":rir_home_LS2_mic, "hospital":rir_hospital_LS2_mic,
                                "meeting":rir_meeting_LS2_mic, "seminar":rir_seminar_LS2_mic}
        self.rir_speaker2mic_dict = {"409":rir_409_speaker2_mic, "819":rir_819_speaker2_mic, "cafe":rir_cafe_speaker2_mic,
                                     "car":rir_car_speaker2_mic, "home":rir_home_speaker2_mic, "hospital":rir_hospital_speaker2_mic,
                                     "meeting":rir_meeting_speaker2_mic, "seminar":rir_seminar_speaker2_mic}
        self.room_list = list(self.rir_LS2mic_dict)
        
        if not os.path.exists(self.out_path_whole):
            os.makedirs(self.out_path_whole)
        if not os.path.exists(self.out_path_e):
            os.makedirs(self.out_path_e)
        if not os.path.exists(self.out_path_s):
            os.makedirs(self.out_path_s)

    def init_model(self, args, model_name, model_options):
        assert model_name is not None, "Model name must be defined!"
        assert "HY_IITP_ESNet" in model_name, \
            "Model name is not supported! Must be one of (HY_IITP_ESNet1,HY_IITP_ESNet2)"
        
        if model_name == "HY_IITP_ESNet1":
            model = HY_IITP_ESNet2_framebyframe(model_options)
            folder_name = './output/%s_%s_%s'%(model_name, args.dataset, args.loss_option)
            model_list = natsort.natsorted(glob.glob(folder_name+'/*'))            
            fin_model = model_list[-1]            
            model.load_state_dict(torch.load(fin_model, map_location='cpu'))
        elif model_name == "HY_IITP_ESNet2" or model_name == "AEC_HY_IITP_ESNet2":
            model = HY_IITP_ESNet2_framebyframe(model_options)   
            folder_name = './output/%s/%s_%s_%s'%(model_name, args.dataset, args.loss_option, self.loss_type)          
            model_list = natsort.natsorted(glob.glob(folder_name+'/*'))
            fin_model = model_list[-1]     
            model.load_state_dict(torch.load(fin_model, map_location='cpu'),strict=False)
        elif "HY_IITP_ESNet3" in model_name:
            model = HY_IITP_ESNet3_framebyframe(model_options)
            folder_name = './output/%s_%s_%s_%s_%schan_%s'%(self.model_name, self.dataset, self.loss_name, str(self.loss_type), str(args.feature_options.chan), args.feature_options.arr)
            model_list = natsort.natsorted(glob.glob(folder_name+'/*'))
            fin_model = model_list[-1]
            model.load_state_dict(torch.load(fin_model, map_location='cpu'))
            
        model.to(self.device)
        return model

    def run(self):
        self.test()
        print("Model test is finished.")

    def test(self):
        
        f = open(self.output_path + '/' + self.model_name + '_' +str(self.feature_options.chan)+'ch_'+self.feature_options.arr+ '.csv','w',newline='\n')
        wr = csv.writer(f)
        
        self.near_model.eval()
        self. far_model.eval()
        
        times = AverageMeter()
        times.reset()
        end = time.time()
        erle_tot = 0

        print()
        print("Algorithmic delay: ", (self.win_size - self.hop_size) /16, " ms")
        print("Buffering delay: ", self.hop_size /16, " ms")
        print("1 frame delay time: ", (self.win_size) / 16, " ms")
        print()
        
        with torch.no_grad():
            for idx, load_path_near_src_nm in enumerate(tqdm(self.load_path_near_src_nm_list, desc=f'test_UEHC.py', dynamic_ncols=True,)):
                
                splited_nm = load_path_near_src_nm.split('/')
                save_nm = splited_nm[-1].split('.wav')[0]
                
                near_SER = int(save_nm.split('_')[-1])
                near_room = splited_nm[-2].split('_')[0]
                far_room = random.choice(self.room_list)
                near_room_rir_LS2mic = self.rir_LS2mic_dict[near_room]
                near_room_rir_speaker2mic = self.rir_speaker2mic_dict[near_room]
                far_room_rir_LS2mic = self.rir_LS2mic_dict[far_room]
                far_room_rir_speaker2mic = self.rir_speaker2mic_dict[far_room]
                
                
                # load wavs
                near_src_wav, sr = sf.read(load_path_near_src_nm, dtype='float32')
                far_src_wav, sr = sf.read(load_path_near_src_nm.replace('/near_src/','/far_src/'), dtype='float32')
                far_src_wav = far_src_wav / max(abs(far_src_wav)) * ( 0.4 + 0.4*random.random() )
                near_noise, sr = sf.read(load_path_near_src_nm.replace('/near_src/','/noise/'), dtype='float32')
                near_noise = torch.from_numpy(near_noise).permute(1,0)
                far_noise_dir = load_path_near_src_nm.replace('/near_src/'+near_room+'_'+str(self.chan)+'ch_'+self.arr+'/'+splited_nm[-1],'/noise/'+far_room+'_'+self.chan+'ch_'+self.arr+'/')
                far_noise_dir = far_noise_dir + re.sub(r'_\d+_SER_-?\d+', '', save_nm)
                far_noise_dir = natsort.natsorted(glob.glob(far_noise_dir+'*.wav'))
                far_noise_dir = random.choice(far_noise_dir)
                far_noise, sr = sf.read(far_noise_dir, dtype='float32')
                far_noise = torch.from_numpy(far_noise).permute(1,0)
                far_split = far_noise_dir.split('.wav')[0].split('/')[-1].split('_')
                far_SER_str = far_split[4]
                far_SER = int(far_SER_str)
                far_idx = far_split[2]
                
                nm = near_room + '_' + save_nm + '_' + far_room + '_' + far_idx + '_' + far_SER_str
                out_save_path = self.out_path_whole + nm + '.wav'
                out_path_e = out_save_path.replace("_output/", "_pesq_e/")
                out_path_s = out_save_path.replace("_output/", "_pesq_s/")
                
                # speaker2mic
                near_wav = torch.from_numpy(ss.convolve(near_room_rir_speaker2mic, near_src_wav[None, ...])[:, :min_len])
                far_wav = torch.from_numpy(ss.convolve(far_room_rir_speaker2mic, far_src_wav[None, ...])[:, :min_len])
                near_noisy_wav = near_noise + near_wav
                far_noisy_wav = far_noise + far_wav
                
                # gains
                near_amp_gain = max(abs( far_wav[0,:]))
                far_amp_gain  = max(abs(near_wav[0,:]))
                vad_src_idx, vad_dst_idx = vad(near_wav[0])
                near_wav_pow = torch.mean(near_wav[0, vad_src_idx:vad_dst_idx] ** 2)
                far_wav_pow  = torch.mean( far_wav[0, vad_src_idx:vad_dst_idx] ** 2)
                near_echo_wav = ss.convolve(near_room_rir_LS2mic, loudspeaker_asymmetric_nonlinear(power_amplifier_clipping( far_wav[0:1,:] / near_amp_gain, self.max_val)) )[:, :min_len]
                far_echo_wav  = ss.convolve( far_room_rir_LS2mic, loudspeaker_asymmetric_nonlinear(power_amplifier_clipping(near_wav[0:1,:] /  far_amp_gain, self.max_val)) )[:, :min_len]
                near_echo_wav_pow = np.mean(near_echo_wav[0, vad_src_idx:vad_dst_idx] ** 2)
                far_echo_wav_pow  = np.mean( far_echo_wav[0, vad_src_idx:vad_dst_idx] ** 2)
                near_SER_gain = np.sqrt( (near_wav_pow / near_echo_wav_pow) * (10 ** (-near_SER/10)) )
                far_SER_gain  = np.sqrt( ( far_wav_pow /  far_echo_wav_pow) * (10 ** (-far_SER /10)) )
                
                # buffer set
                near_hn = torch.zeros(2,1,256)
                near_cn = torch.zeros(2,1,256)
                far_hn = torch.zeros(2,1,256)
                far_cn = torch.zeros(2,1,256)
                d_n = torch.zeros(self.feature_options.chan, self.hop_size)
                d_f = torch.zeros(self.feature_options.chan, self.hop_size)
                near_ref = torch.zeros(self.win_size)
                far_ref  = torch.zeros(self.win_size)
                near_mic = torch.zeros(self.feature_options.chan, self.win_size)
                far_mic  = torch.zeros(self.feature_options.chan, self.win_size)
                near_rir_buf = np.zeros(self.hop_size + 511)
                far_rir_buf  = np.zeros(self.hop_size + 511)
                block_num = min_len // self.hop_size
                near_spec = torch.zeros(self.feature_options.chan, 258, 1)
                far_spec = torch.zeros(self.feature_options.chan, 258, 1)
                
                # Mic. and Ref. and out to save
                mic = torch.zeros(self.feature_options.chan, min_len)
                out = torch.zeros(0)
                
                # 1st block process (Ref. signal is generated after 2nd block)
                near_mic[:, self.hop_size:] = near_noisy_wav[:,self.hop_size * 0 : self.hop_size * 1]
                far_mic [:, self.hop_size:] = far_noisy_wav [:,self.hop_size * 0 : self.hop_size * 1]
                mic[:, :self.hop_size] = near_mic[:, self.hop_size:]
                near_wav_hat, near_hn, near_cn, near_spec = self.near_model(near_mic, near_ref[None, ...], near_hn, near_cn, near_spec)
                far_wav_hat ,  far_hn,  far_cn,  far_spec = self.far_model( far_mic , far_ref [None, ...],  far_hn,  far_cn,  far_spec)
                
                for idx in range(1, block_num):
                    # mic in (sum of clean & echo)
                    near_mic = torch.roll(near_mic, -self.hop_size, [1])
                    near_mic[:, self.hop_size:] = near_noisy_wav[:,self.hop_size * idx:self.hop_size * (idx+1)] + d_n
                    far_mic  = torch.roll( far_mic, -self.hop_size, [1])
                    far_mic [:, self.hop_size:] = far_noisy_wav [:,self.hop_size * idx:self.hop_size * (idx+1)] + d_f
                    mic[:, self.hop_size * idx : self.hop_size * (idx+1)] = near_mic[:, self.hop_size:]
                    
                    # UEHC
                    near_wav_hat, near_hn, near_cn, near_spec = self.near_model(near_mic, near_ref[None, ...], near_hn, near_cn, near_spec)
                    out = torch.cat([out, near_wav_hat], dim=1)
                    far_wav_hat ,  far_hn,  far_cn,  far_spec = self.far_model( far_mic , far_ref [None, ...],  far_hn,  far_cn,  far_spec)
                    
                    # amp gain
                    near_ref = torch.roll(near_ref, -self.hop_size)
                    near_ref[self.hop_size:] =  far_wav_hat[0] / near_amp_gain   # near_end ref. signal
                    far_ref  = torch.roll( far_ref, -self.hop_size)
                    far_ref[self.hop_size:]  = near_wav_hat[0] /  far_amp_gain   #  far_end ref. signal
                    
                    # NL_AEC_system & echo & SER gain
                    near_rir_buf = np.roll(near_rir_buf, -self.hop_size)
                    far_rir_buf  = np.roll( far_rir_buf, -self.hop_size)
                    near_rir_buf[-self.hop_size:] = loudspeaker_asymmetric_nonlinear(power_amplifier_clipping(near_ref[self.hop_size:], self.max_val))
                    far_rir_buf [-self.hop_size:] = loudspeaker_asymmetric_nonlinear(power_amplifier_clipping( far_ref[self.hop_size:], self.max_val))
                    d_n = near_SER_gain * torch.from_numpy(ss.convolve(near_room_rir_LS2mic, near_rir_buf[None, ...])[:, 511 : 511 + self.hop_size])
                    d_f =  far_SER_gain * torch.from_numpy(ss.convolve( far_room_rir_LS2mic,  far_rir_buf[None, ...])[:, 511 : 511 + self.hop_size])
                
                near_mic = torch.roll(near_mic, -self.hop_size, [1])
                near_mic[:, self.hop_size:] = torch.zeros(self.feature_options.chan, self.hop_size)
                near_wav_hat, near_hn, near_cn, near_spec = self.near_model(near_mic, near_ref[None, ...], near_hn, near_cn, near_spec)
                out = torch.cat([out, near_wav_hat], dim=1)
                er_tot = 0
                for er in range(int(self.chan)):
                    src_idx, dst_idx = vad(near_wav[er])
                    er_tot = er_tot + ERLE(mic[er], out[er], src_idx, dst_idx).item()
                
                erle = er_tot / int(self.chan)
                erle_tot = erle + erle_tot
                pesq_tot = 0
                for chan_idx in range(int(self.chan)):
                    pesq = pypesq.pesq(near_wav[chan_idx,vad_src_idx:vad_dst_idx], out[chan_idx,vad_src_idx:vad_dst_idx], 16000)
                    pesq_tot = pesq + pesq_tot
                sf.write(out_save_path, out.T, 16000, "PCM_16")
                sf.write(out_path_e, out[:,vad_src_idx:vad_dst_idx].T, self.sampling_rate, subtype='PCM_16')
                sf.write(out_path_s, near_wav[:,vad_src_idx:vad_dst_idx].T, self.sampling_rate, subtype='PCM_16')                
                
                wr.writerow([ nm, erle, pesq_tot / int(self.chan)] )
                
        print("Average of the ERLE: ", erle_tot / len(self.load_path_near_src_nm_list))
        print("\n")


def main():
    parser = argparse.ArgumentParser(description='Parse the config path')
    parser.add_argument("-c", "--config", dest="path", default='./configs/test_UEHC.json',
                        help='The path to the config file. e.g. python train.py -c configs/test.json')
    parser.add_argument("-l", "--loss_type", type=int, default='0')

    config = parser.parse_args()
    with open(config.path) as f:
        args = json.load(f)
        args = AttrDict(args)
    t = tester(args, config.loss_type)
    t.run()


if __name__ == "__main__":
    main()

