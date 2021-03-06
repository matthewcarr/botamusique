#!/usr/bin/env python3

import threading
import time
import sys
import signal
import configparser
import audioop
import subprocess as sp
import argparse
import os.path
from os import listdir
import pymumble.pymumble_py3 as pymumble
import interface
import variables as var

class MumbleBot:
    def __init__(self):
        signal.signal(signal.SIGINT, self.ctrl_caught)

        self.config = configparser.ConfigParser(interpolation=None)
        self.config.read("configuration.ini", encoding='utf-8')

        parser = argparse.ArgumentParser(description='Bot for playing audio files on Mumble')
        parser.add_argument("-s", "--server", dest="host", type=str, required=True, help="The hostame of a mumble server")
        parser.add_argument("-u", "--user", dest="user", type=str, required=True, help="Username for the bot", default="botamusique")
        parser.add_argument("-P", "--password", dest="password", type=str, default="", help="Password if server requires one")
        parser.add_argument("-p", "--port", dest="port", type=int, default=64738, help="Port of the mumble server")
        parser.add_argument("-c", "--channel", dest="channel", type=str, default="", help="Starting channel for the bot")

        args = parser.parse_args()
        self.volume = self.config.getfloat('bot', 'volume')

        self.channel = args.channel
        var.current_music = None
        var.playlist = []
        var.user = args.user
        var.music_folder = self.config.get('bot', 'music_folder')
        var.filters = self.config['filters']
        var.filter_select = self.config.get('bot', 'filter')
        var.is_proxified = self.config.getboolean("bot", "is_proxified")
        self.exit = False
        self.nb_exit = 0
        self.thread = None

        interface.init_proxy()

        t = threading.Thread(target=start_web_interface)
        t.daemon = True
        t.start()

        self.mumble = pymumble.Mumble(args.host, user=args.user, port=args.port, password=args.password,
                                      debug=self.config.getboolean('debug', 'mumbleConnection'))
        self.mumble.callbacks.set_callback("text_received", self.message_received)

        self.mumble.set_codec_profile("audio")
        self.mumble.start()  # start the mumble thread
        self.mumble.is_ready()  # wait for the connection
        self.set_comment()
        self.mumble.users.myself.unmute()  # be sure the user is not muted
        if self.channel:
            self.mumble.channels.find_by_name(self.channel).move_in()
        self.mumble.set_bandwidth(200000)

        self.loop()

    def ctrl_caught(self, signal, frame):
        print("\nInterrupt Received, Stopping")
        self.exit = True
        self.stop()
        if self.nb_exit > 1:
            print("Forced Kill")
            sys.exit(0)
        self.nb_exit += 1

    def message_received(self, text):
        message = text.message
        if message[0] == '!':
            message = message[1:].split(' ', 1)
            if len(message) > 0:
                command = message[0]
                parameter = ''
                if len(message) > 1:
                    parameter = message[1]
            else:
                return

            print(command + ' - ' + parameter + ' by ' + self.mumble.users[text.actor]['name'])

            if command == self.config.get('command', 'play_file') and parameter:
                if ".." in parameter:
                    self.send_msg_channel(self.config.get('strings', 'naughty') % (
                        self.mumble.users[text.actor]['name']))
                    return
                path = self.config.get('bot', 'music_folder') + parameter
                if os.path.isfile(path):
                    self.launch_play_file(path)
                elif os.path.isdir(path):
                    self.mumble.users[text.actor].send_message(self.config.get('strings', 'bad_file'))
                else:
                    self.mumble.users[text.actor].send_message(self.config.get('strings', 'no_file'))

            elif command == self.config.get('command', 'stop'):
                self.stop()

            elif command == self.config.get('command', 'kill'):
                if self.is_admin(text.actor):
                    self.stop()
                    self.exit = True
                else:
                    self.mumble.users[text.actor].send_message(self.config.get('strings', 'not_admin'))

            elif command == self.config.get('command', 'stop_and_getout'):
                self.stop()
                if self.channel:
                    self.mumble.channels.find_by_name(self.channel).move_in()

            elif command == self.config.get('command', 'joinme'):
                self.mumble.users.myself.move_in(self.mumble.users[text.actor]['channel_id'])

            elif command == self.config.get('command', 'volume'):
                if parameter is not None and parameter.isdigit() and 0 <= int(parameter) <= 100:
                    self.volume = float(float(parameter) / 100)
                    self.send_msg_channel(self.config.get('strings', 'change_volume') % (
                        int(self.volume * 100), self.mumble.users[text.actor]['name']))
                else:
                    self.send_msg_channel(self.config.get('strings', 'current_volume') % int(self.volume * 100))

            elif command == self.config.get('command', 'filters'):
                if parameter is not None and parameter in var.filters:
                    var.filter_select = parameter
                    self.send_msg_channel(self.config.get('strings', 'change_filter') % (
                        var.filter_select, self.mumble.users[text.actor]['name']))
                else:
                    self.send_msg_channel(self.config.get('strings', 'current_filter') % (
                        var.filter_select, ', '.join(var.filters)))

            elif command == self.config.get('command', 'current_music'):
                if var.current_music is not None:
                    now_playing = var.current_music.replace(var.music_folder, "./") # hide full path from users
                    self.send_msg_channel(now_playing)

                else:
                    self.mumble.users[text.actor].send_message(self.config.get('strings', 'not_playing'))

            elif command == self.config.get('command', 'list') and parameter: # list files in subfolder
                if ".." in parameter:
                    self.send_msg_channel(self.config.get('strings', 'naughty') % (
                        self.mumble.users[text.actor]['name']))
                    return

                folder_path = self.config.get('bot', 'music_folder') + parameter
                if os.path.isdir(folder_path):
                    files = sorted([f for f in listdir(folder_path)])
                    if len(files) > 20 :
                        for x in range(0, len(files), 20):
                            self.mumble.users[text.actor].send_message('<br>'.join(files[x:x+20]))
                    else:
                        self.mumble.users[text.actor].send_message('<br>'.join(files))
                else:
                    self.mumble.users[text.actor].send_message(self.config.get('strings', 'no_dir'))

            elif command == self.config.get('command', 'list'): # list subfolders
                folder_path = self.config.get('bot', 'music_folder')
                dirs = [f for f in listdir(folder_path) if os.path.isdir(os.path.join(folder_path, f))]
                self.mumble.users[text.actor].send_message('<br>'.join(dirs))

            elif command == self.config.get('command', 'skip'):
                if (len(var.playlist) == 0):
                    self.stop()
                else: 
                    var.current_music = var.playlist.pop(0)
                    self.launch_play_file()

            elif command == self.config.get('command', 'texttospeech') and parameter:
                if len(parameter) > int(self.config.get('bot', 'tts_limit')): # over 1000 chars
                    self.mumble.users[text.actor].send_message(self.config.get('strings', 'tts_longtext') % (
                        int(self.config.get('bot', 'tts_limit'))))
                else:
                    tts_process = sp.run(["text2wave", "-F 44100 -o", self.config.get('bot', 'music_folder') + self.config.get('bot', 'tts_folder') + "/" + self.mumble.users[text.actor]['name']], input = parameter, text = True))
                    self.mumble.users[text.actor].send_message(self.config.get('strings', 'tts_success') % (
                        self.config.get('bot', 'tts_folder'), self.mumble.users[text.actor]['name']))

            else:
                self.mumble.users[text.actor].send_message(self.config.get('strings', 'bad_command'))

    def is_admin(self, user):
        username = self.mumble.users[user]['name']
        list_admin = self.config.get('bot', 'admin').split(';')
        if username in list_admin:
            return True
        else:
            return False

    def launch_play_file(self, path=None):
        if not path:
            path = self.config.get('bot', 'music_folder') + var.current_music
        if self.config.getboolean('debug', 'ffmpeg'):
            ffmpeg_debug = "debug"
        else:
            ffmpeg_debug = "warning"
        command = ["ffmpeg", '-v', ffmpeg_debug, '-nostdin', '-i', path, '-filter:a', var.filters[var.filter_select], '-ac', '1', '-f', 's16le', '-ar', '48000', '-']
        if self.thread: # make sure old thread is gone first
            self.thread.kill()
            self.thread = None
        self.thread = sp.Popen(command, stdout=sp.PIPE, bufsize=480)
        var.current_music = path

    def loop(self):
        raw_music = 0
        while not self.exit:

            while self.mumble.sound_output.get_buffer_size() > 0.5: # wait for buffer to be used up
                time.sleep(0.01)
            if self.thread: # read from thread if present
                raw_music = self.thread.stdout.read(480)
                if raw_music: # if thread had output i.e. pcm audio
                    self.mumble.sound_output.add_sound(audioop.mul(raw_music, 2, self.volume)) # add it to the buffer
                else:
                    time.sleep(0.1)
            else:
                time.sleep(0.1)

            if self.thread is None or not raw_music: # when nothing is playing
                if len(var.playlist) != 0: # play next track if there is a list
                    var.current_music = var.playlist.pop(0)
                    self.launch_play_file()
                else:
                    self.stop() # clean up the old thread and current_msuic at end of playlist

        while self.mumble.sound_output.get_buffer_size() > 0:
            time.sleep(0.01)
        time.sleep(0.5)

    def stop(self):
        var.current_music = None
        var.playlist = []
        if self.thread:
            self.thread.kill()
            self.thread = None

    def set_comment(self):
        self.mumble.users.myself.comment(self.config.get('bot', 'comment'))

    def send_msg_channel(self, msg, channel=None):
        if not channel:
            channel = self.mumble.channels[self.mumble.users.myself['channel_id']]
        channel.send_text_message(msg)

def start_web_interface():
    interface.web.run(port=8181, host="127.0.0.1")

if __name__ == '__main__':
    botamusique = MumbleBot()
