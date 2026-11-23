# 은행 약관 개인정보 분석

공개된 개인정보 문서를 OpenAI API로 분석하고 여섯 항목의 인용문이 입력에 정확히 존재하는지 검사하는 실험 프로젝트입니다. Python 3.10 이상이 필요하며 추가 패키지는 없습니다. 분석할 본문은 OpenAI API로 전송됩니다. 실제 신청서나 개인정보가 포함된 문서는 사용하지 마세요.

## 독립 실행 분석

`sample.txt`는 [하나은행의 공개 동의서](https://image.kebhana.com/cont/customer/customer07/customer0701/customer070106/__icsFiles/afieldfile/2025/08/20/3-08-1294.pdf)에서 추출한 텍스트입니다. PDF의 표 제목과 내용이 대응하도록 줄 배치를 조정했고, 문단과 관련 항목 사이에 빈 줄을 넣었습니다. PDF를 직접 읽는 기능은 없습니다.

PowerShell에서 키를 화면에 표시하지 않고 현재 프로세스에만 설정한 뒤 실행합니다. 실제 호출에는 API 사용 권한과 네트워크 연결이 필요하고 비용이 발생합니다.

```powershell
$secret = Read-Host 'OpenAI API 키' -AsSecureString
$secretPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try { $env:OPENAI_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPtr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPtr); Remove-Variable secret, secretPtr }
$env:OPENAI_MODEL = 'gpt-4.1-mini'
python .\analyze.py .\sample.txt
```

성공하면 결과를 화면과 `result.json`에 출력합니다. 다른 파일을 지정하려면 `--output 경로`를 추가하세요. 빈 줄로 구분된 블록마다 문단 번호를 부여하고 인용문이 해당 문단에 정확히 있는지 검사합니다. 인용이 틀리면 해당 항목을 `근거 확인 실패`로 표시하고 종료 코드 1을 반환합니다. `입력에서 확인되지 않음`은 입력에서 관련 내용을 찾지 못했다는 의미이며, 실제 부재를 보장하지 않습니다. 인용이 일치해도 설명의 정확성이나 조건·예외의 보존을 보장하지 않으므로 원문과 대조해야 합니다.

API, 응답 또는 파일 오류 시 결과를 저장하지 않습니다. 이전 결과가 남아 있을 수 있으므로 수정 시각을 확인하세요. 종료 코드는 성공 0, 오류 또는 근거 확인 실패 1, 설정·입력 범위 오류 2입니다. 키 파일은 자동으로 읽지 않으며, 키를 코드나 채팅에 적지 마세요.

## 로컬 웹 실험

`python app.py`로 로컬 서버를 시작할 수 있습니다. 웹 분석기는 독립 실행 분석기와 별도의 결과 형식과 문단 처리 방식을 사용합니다. 운영 보안 검증이 끝나지 않았으므로 공개 배포하지 마세요.

## 테스트

```powershell
python -m unittest discover -s tests -v
```

긴 문서의 문맥, 표 구조, 교차 참조 및 설명의 의미 정확도는 별도 평가가 필요합니다.
