FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src
COPY SampleService.csproj .
RUN dotnet restore SampleService.csproj
COPY . .
RUN dotnet publish SampleService.csproj -c Release -o /app/publish --no-restore

FROM mcr.microsoft.com/dotnet/aspnet:10.0 AS final
WORKDIR /app
ENV ASPNETCORE_HTTP_PORTS=8000
COPY --from=build /app/publish .
USER $APP_UID
EXPOSE 8000
ENTRYPOINT ["dotnet", "SampleService.dll"]
