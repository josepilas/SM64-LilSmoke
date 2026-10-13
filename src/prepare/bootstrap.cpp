// SPDX-License-Identifier: MPL-2.0
#include <SDL.h>
#include <webp/decode.h>
#include <json-c/json.h>
#include "input_bridge.h"
#include <algorithm>
#include <cerrno>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>
#include <sys/wait.h>
#include <sys/resource.h>
#include <unistd.h>
#include <vector>

namespace {
using GLbyte = signed char;
#include "font8x8_basic.h"
using Present = int (*)(const void *, unsigned, unsigned, unsigned, int, unsigned, unsigned);
struct NativeInput {
    NativeInput() { sm64_native_input_init(); }
    ~NativeInput() { sm64_native_input_close(); }
};
struct Screen {
    SDL_Window *window = nullptr;
    SDL_Surface *canvas = nullptr, *icon = nullptr;
    std::vector<unsigned char> image;
    Present present = nullptr;
    Screen(const std::filesystem::path& app) {
        if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_EVENTS | SDL_INIT_GAMECONTROLLER) != 0)
            throw std::runtime_error(SDL_GetError());
        window = SDL_CreateWindow("Super Mario 64", 0, 0, 640, 360, SDL_WINDOW_SHOWN);
        canvas = SDL_CreateRGBSurfaceWithFormat(0, 640, 360, 16, SDL_PIXELFORMAT_RGB565);
        if (!window || !canvas) throw std::runtime_error(SDL_GetError());
        SDL_ShowCursor(SDL_DISABLE);
        present = reinterpret_cast<Present>(dlsym(RTLD_DEFAULT, "lilsmoke_present_rgb"));
        const auto path = app / "icon.webp";
        if (!std::filesystem::is_symlink(path) && std::filesystem::is_regular_file(path)) {
            const auto bytes = std::filesystem::file_size(path);
            if (bytes > 12 && bytes <= 512 * 1024) {
                std::vector<unsigned char> encoded(bytes);
                std::ifstream input(path, std::ios::binary);
                int width = 0, height = 0;
                if (input.read(reinterpret_cast<char*>(encoded.data()), encoded.size()) &&
                    WebPGetInfo(encoded.data(), encoded.size(), &width, &height) && width == 128 && height == 128) {
                    image.resize(128 * 128 * 4);
                    if (WebPDecodeRGBAInto(encoded.data(), encoded.size(), image.data(), image.size(), 128 * 4))
                        icon = SDL_CreateRGBSurfaceWithFormatFrom(image.data(), 128, 128, 32, 128 * 4, SDL_PIXELFORMAT_RGBA32);
                    if (icon) std::fprintf(stderr,"NativeLoadingIcon=WebP Size=128x128\n");
                }
            }
        }
    }
    ~Screen() {
        SDL_FreeSurface(icon);
        SDL_FreeSurface(canvas);
        SDL_DestroyWindow(window);
        SDL_Quit();
    }
    void text(const std::string& value, int x, int y, unsigned color, int scale=2) {
        for (unsigned char letter : value) {
            if (letter >= 128) letter = '?';
            for (int row=0; row<8; ++row) for (int column=0; column<8; ++column)
                if (font8x8_basic[letter][row] & (1 << column)) {
                    SDL_Rect pixel{x + column*scale, y + row*scale, scale, scale};
                    SDL_FillRect(canvas, &pixel, color);
                }
            x += 8*scale;
        }
    }
    void centered(const std::string& value, int y, unsigned color, int scale=2) {
        text(value, (640-int(value.size())*8*scale)/2, y, color, scale);
    }
    void draw(const std::string& phase, int percent, double bytes, bool error=false) {
        auto color=[&](int r,int g,int b){ return SDL_MapRGB(canvas->format,r,g,b); };
        SDL_FillRect(canvas, nullptr, color(240,244,247));
        if (icon) { SDL_Rect rect{280,24,80,80}; SDL_BlitScaled(icon,nullptr,canvas,&rect); }
        centered("Super Mario 64",128,color(24,34,45));
        const auto line=phase.substr(0,37);
        centered(line,169,error?color(170,35,35):color(67,80,92));
        SDL_Rect track{48,218,544,16}; SDL_FillRect(canvas,&track,color(203,211,218));
        SDL_Rect bar{48,218,544*std::clamp(percent,0,100)/100,16};
        SDL_FillRect(canvas,&bar,color(22,139,224));
        char buffer[80]; std::snprintf(buffer,sizeof(buffer),"%d%%",percent);
        centered(buffer,253,color(24,34,45),3);
        std::snprintf(buffer,sizeof(buffer),"%.2f MB extracted",bytes/1000000.0);
        centered(buffer,298,color(67,80,92));
        if (!error && percent<100) centered("Cancel",334,color(67,80,92),1);
        if (present && present(canvas->pixels,640,360,canvas->pitch,0,16,9)) return;
        SDL_Surface *surface=SDL_GetWindowSurface(window);
        if (!surface || SDL_BlitScaled(canvas,nullptr,surface,nullptr) || SDL_UpdateWindowSurface(window))
            throw std::runtime_error(SDL_GetError());
    }
};

std::string json_string(json_object *object, const char *key) {
    json_object *value=nullptr;
    if (!json_object_object_get_ex(object,key,&value) || !json_object_is_type(value,json_type_string)) return {};
    return json_object_get_string(value);
}
}

int main(int argc,char **argv) {
    pid_t worker=-1;
    int input=-1;
    bool worker_reaped=false;
    try {
        if (argc<2 || argc>3) throw std::runtime_error("Application directory required");
        auto app=std::filesystem::canonical(argv[1]);
        const bool ui_test=argc==3 && std::string(argv[2])=="--ui-test";
        int output[2]={-1,-1};
        if (!ui_test) {
            if (pipe(output)) throw std::runtime_error("Cannot create preparation pipe");
            const auto python=(app/"runtime/python/usr/bin/python3.11").string();
            const auto backend=(app/"runtime/native_build.py").string();
            const auto home=(app/"runtime/python/usr").string();
            setenv("PYTHONHOME",home.c_str(),1);
            setenv("PYTHONDONTWRITEBYTECODE","1",1);
            worker=fork();
            if (worker<0) throw std::runtime_error("Cannot start preparation worker");
            if (!worker) {
                close(output[0]);
                dup2(output[1],STDOUT_FILENO);
                close(output[1]);
                execl(python.c_str(),python.c_str(),"-u",backend.c_str(),"--app",app.c_str(),static_cast<char*>(nullptr));
                _exit(126);
            }
            close(output[1]); input=output[0];
            fcntl(input,F_SETFL,fcntl(input,F_GETFL)|O_NONBLOCK);
        }
        std::string ready, error, phase="Checking assets", pending;
        int percent=0, status=0;
        double bytes=0;
        bool cancelled=false, eof=false;
        {
            Screen screen(app);
            NativeInput controls;
            const auto started=SDL_GetTicks();
            while (true) {
                if (sm64_native_input_poll() || (SDL_GetTicks()-started>300 && (sm64_native_input_menu_buttons()&32))) {
                    if (!cancelled && worker>0) kill(worker,SIGTERM);
                    cancelled=true; phase="Cancelling";
                }
                if (ui_test) {
                    screen.draw("Extracting assets",73,5864321);
                    if (SDL_SaveBMP(screen.canvas,(app/"extraction-ui-test.bmp").c_str()))
                        throw std::runtime_error(SDL_GetError());
                    break;
                }
                char buffer[8192];
                ssize_t count;
                while ((count=read(input,buffer,sizeof(buffer)))>0) {
                    pending.append(buffer,count);
                    if (pending.size()>65536) throw std::runtime_error("Invalid preparation progress stream");
                    std::size_t newline;
                    while ((newline=pending.find('\n'))!=std::string::npos) {
                        auto line=pending.substr(0,newline); pending.erase(0,newline+1);
                        json_object *object=json_tokener_parse(line.c_str());
                        if (!object) continue;
                        const auto next=json_string(object,"phase"); if (!next.empty()) phase=next;
                        const auto result=json_string(object,"ready"); if (!result.empty()) ready=result;
                        const auto failure=json_string(object,"error"); if (!failure.empty()) error=failure;
                        json_object *value=nullptr;
                        if (json_object_object_get_ex(object,"percent",&value) && json_object_is_type(value,json_type_int))
                            percent=std::clamp(json_object_get_int(value),0,100);
                        if (json_object_object_get_ex(object,"extracted_bytes",&value) && json_object_is_type(value,json_type_int))
                            bytes=std::clamp<double>(json_object_get_int64(value),0,128*1024*1024);
                        json_object_put(object);
                    }
                }
                if (count==0) eof=true;
                screen.draw(phase,percent,bytes);
                if (!worker_reaped) {
                    pid_t result=waitpid(worker,&status,WNOHANG);
                    if (result==worker) worker_reaped=true;
                    if (result<0 && errno!=EINTR) throw std::runtime_error("Lost preparation worker");
                }
                if (worker_reaped && eof) break;
                SDL_Delay(33);
            }
            if (!ui_test && !cancelled && (!WIFEXITED(status) || WEXITSTATUS(status)!=0 || ready.empty())) {
                screen.draw(error.empty()?"Preparation failed":error,percent,bytes,true);
                sm64_native_input_suppress_buttons();
                const auto until=SDL_GetTicks()+10000;
                while (SDL_GetTicks()<until) {
                    if (sm64_native_input_poll() || sm64_native_input_menu_buttons()) break;
                    SDL_Delay(16);
                }
                throw std::runtime_error(error.empty()?"Preparation failed":error);
            }
        }
        if (input>=0) { close(input); input=-1; }
        if (ui_test || cancelled) return cancelled?130:0;
        const auto game=std::filesystem::canonical(ready);
        const auto relative=game.lexically_relative(app/".sm64-cache");
        if (relative.empty() || *relative.begin()==".." || game.filename()!="game")
            throw std::runtime_error("Prepared game is outside the private cache");
        const auto saves=app/"saves";
        std::filesystem::create_directories(saves);
        setenv("HOME",saves.c_str(),1);
        unsetenv("PYTHONHOME");
        const rlimit game_memory{65536*1024,65536*1024};
        if (setrlimit(RLIMIT_AS,&game_memory)) throw std::runtime_error("Cannot apply game memory limit");
        if (chdir(saves.c_str())) throw std::runtime_error("Cannot open save directory");
        execl(game.c_str(),game.c_str(),static_cast<char*>(nullptr));
        throw std::runtime_error("Cannot start prepared game");
    } catch (const std::exception& error) {
        if (worker>0 && !worker_reaped) { kill(worker,SIGTERM); waitpid(worker,nullptr,0); }
        if (input>=0) close(input);
        std::fprintf(stderr,"SM64 preparation: %s\n",error.what());
        return 1;
    }
}
